"""
=================================================================
  KARAOKE ONLINE — Main Server (FastAPI)
  Port     : 8001
  Protokol :
    • UDP  :5004   ← Terima audio mentah dari Proxy
    • WS   /ws/score → Kirim skor real-time ke Browser
    • HTTP /api/*  → REST endpoints (songs, sessions, lyrics)
    • HTTP /media/ → Static file (video MP4, audio)

  Pipeline audio per chunk (~85ms):
    UDP buffer → VoiceSeparator → YIN Pitch → Score → WebSocket
=================================================================
"""

import os, sys, json, wave, asyncio, socket
from datetime import datetime
from typing import Optional, List

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session as DBSession

# ── Tambahkan root project ke path agar bisa import modul sibling ──
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from database import get_db, Song, User, Session as KaraokeSession, PitchLog, NetworkStat
from pitch_utils import yin_pitch, freq_to_note, score_pitch, get_grade, get_pitch_label

# ── Coba import VoiceSeparator (opsional) ─────────────────────────
try:
    from voice_separator import VoiceSeparator
    _voice_sep = VoiceSeparator()
    HAS_SEP = True
    print("[VoiceSep] VoiceSeparator berhasil dimuat — mode Wiener/SS aktif")
except Exception as e:
    _voice_sep = None
    HAS_SEP = False
    print(f"[VoiceSep] Tidak tersedia ({e}) — audio mentah digunakan")

# ─────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────
UDP_IP       = "127.0.0.1"
UDP_PORT     = 5004
SAMPLE_RATE  = 48000
CHUNK_BYTES  = 4096 * 2          # 4096 sample × 2 bytes (int16) ≈ 85ms
PROCESS_INTERVAL = 0.09          # Interval proses audio (detik)

MEDIA_DIR  = os.path.join(os.path.dirname(__file__), "music", "media")
LYRIC_DIR  = os.path.join(os.path.dirname(__file__), "music", "lyric")
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(LYRIC_DIR, exist_ok=True)

# ─────────────────────────────────────────────
#  STATE GLOBAL
# ─────────────────────────────────────────────
class AppState:
    # Audio
    audio_buffer: bytearray = bytearray()
    udp_running: bool = False

    # Sesi karaoke aktif
    session_id: Optional[int] = None
    session_start_ms: Optional[int] = None
    song_pitch_ref: list = []      # [(t_ms, hz), ...]
    accumulated_score: float = 0.0
    total_frames: int = 0
    scored_frames: int = 0         # Frame di mana user sedang bernyanyi

    # WebSocket clients untuk streaming skor
    score_clients: List[WebSocket] = []

    # Statistik jaringan (untuk demo Wireshark)
    packets_received: int = 0
    bytes_received: int = 0
    last_pitch_hz: float = 0.0
    sep_mode: str = "raw"

state = AppState()

# ─────────────────────────────────────────────
#  UDP LISTENER (background task)
# ─────────────────────────────────────────────
async def udp_listener_task():
    """Mendengarkan paket UDP dari Proxy dan mengumpulkan audio ke buffer."""
    state.udp_running = True
    loop = asyncio.get_running_loop()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.setblocking(False)
    print(f"[UDP] Listener aktif di {UDP_IP}:{UDP_PORT}")

    try:
        while state.udp_running:
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 16384), timeout=1.0
                )
                state.audio_buffer.extend(data)
                state.packets_received += 1
                state.bytes_received += len(data)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        pass
    finally:
        sock.close()
        print("[UDP] Listener dihentikan.")


# ─────────────────────────────────────────────
#  AUDIO PROCESSOR (background task)
# ─────────────────────────────────────────────
async def audio_processor_task():
    """
    Setiap PROCESS_INTERVAL:
      1. Ambil chunk dari buffer
      2. Voice Separator
      3. YIN Pitch Detection
      4. Hitung skor vs referensi
      5. Broadcast ke WebSocket clients
    """
    print("[Processor] Audio processor dimulai.")
    while True:
        await asyncio.sleep(PROCESS_INTERVAL)

        if len(state.audio_buffer) < CHUNK_BYTES:
            continue

        # Ambil chunk
        chunk = bytes(state.audio_buffer[:CHUNK_BYTES])
        state.audio_buffer = state.audio_buffer[CHUNK_BYTES:]

        # Konversi int16 PCM → float32 [-1, 1]
        try:
            audio_i16 = np.frombuffer(chunk, dtype=np.int16)
            audio_f32 = audio_i16.astype(np.float32) / 32768.0
        except Exception:
            continue

        sep_mode = "raw"

        # ── Voice Separator ──────────────────────────────────────
        if HAS_SEP and _voice_sep is not None:
            try:
                res = _voice_sep.process(audio_f32)
                vocal = res.get("vocal", audio_f32)
                sep_mode = res.get("mode", "ss")
                audio_f32 = vocal
            except Exception:
                pass

        state.sep_mode = sep_mode

        # ── YIN Pitch Detection ───────────────────────────────────
        pitch_hz = yin_pitch(audio_f32, SAMPLE_RATE)
        note_info = freq_to_note(pitch_hz) if pitch_hz else None

        # ── Volume (RMS) ──────────────────────────────────────────
        rms = float(np.sqrt(np.mean(audio_f32 ** 2)))
        is_singing = rms > 0.008 and pitch_hz is not None

        # ── Skor per frame ────────────────────────────────────────
        frame_score = 0.0
        ref_hz = 0.0
        cents_diff = None

        state.total_frames += 1
        if is_singing:
            state.scored_frames += 1

        if is_singing and state.session_id is not None:
            now_ms = int(datetime.now().timestamp() * 1000)
            elapsed_ms = now_ms - (state.session_start_ms or now_ms)

            # Cari referensi pitch terdekat (dalam jendela ±500ms)
            if state.song_pitch_ref:
                closest = min(
                    state.song_pitch_ref,
                    key=lambda ev: abs(ev[0] - elapsed_ms)
                )
                if abs(closest[0] - elapsed_ms) < 500 and closest[1] > 0:
                    ref_hz = closest[1]
                    frame_score = score_pitch(pitch_hz, ref_hz)
                    cents_diff = (
                        1200.0 * np.log2(pitch_hz / ref_hz)
                        if ref_hz > 0 and pitch_hz > 0 else None
                    )
                else:
                    frame_score = 60.0  # Tidak ada ref → base score
            else:
                # Tidak ada referensi → skor berdasarkan stabilitas pitch
                frame_score = min(80.0, 40.0 + rms * 400.0)

            state.accumulated_score += frame_score

        # ── Running average skor ──────────────────────────────────
        session_score = 0.0
        if state.scored_frames > 0:
            session_score = state.accumulated_score / state.scored_frames

        state.last_pitch_hz = pitch_hz or 0.0

        # ── Broadcast ke semua WebSocket klien ───────────────────
        payload = {
            "pitch_hz":      round(pitch_hz, 2) if pitch_hz else 0,
            "note":          note_info["name"] if note_info else "--",
            "note_short":    note_info["note"] if note_info else "--",
            "cents":         note_info["cents"] if note_info else 0,
            "cents_diff":    round(cents_diff, 1) if cents_diff is not None else None,
            "ref_hz":        round(ref_hz, 2),
            "rms":           round(float(rms), 4),
            "is_singing":    bool(is_singing),
            "frame_score":   round(frame_score, 1),
            "session_score": round(session_score, 1),
            "sep_mode":      sep_mode,
            "label":         get_pitch_label(cents_diff) if cents_diff is not None else "",
            "ts_ms":         int(datetime.now().timestamp() * 1000),
        }

        dead = []
        for ws in state.score_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                state.score_clients.remove(ws)
            except ValueError:
                pass


# ─────────────────────────────────────────────
#  LIFESPAN (startup / shutdown)
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    udp_task  = asyncio.create_task(udp_listener_task())
    proc_task = asyncio.create_task(audio_processor_task())
    yield
    # Shutdown
    state.udp_running = False
    udp_task.cancel()
    proc_task.cancel()
    print("[Server] Dimatikan.")


# ─────────────────────────────────────────────
#  FASTAPI APP
# ─────────────────────────────────────────────
app = FastAPI(title="Karaoke Online Server", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
if os.path.isdir(MEDIA_DIR):
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


# ─────────────────────────────────────────────
#  WEBSOCKET — Real-time Score Stream
# ─────────────────────────────────────────────
@app.websocket("/ws/score")
async def ws_score(websocket: WebSocket):
    await websocket.accept()
    state.score_clients.append(websocket)
    print(f"[WS/score] Client terhubung. Total: {len(state.score_clients)}")
    try:
        # Kirim hello agar Wireshark terlihat handshake
        await websocket.send_json({
            "type": "connected",
            "msg": "Karaoke score stream aktif",
            "sep_available": HAS_SEP,
        })
        while True:
            # Tunggu pesan dari client (misalnya session_start signal)
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        try:
            state.score_clients.remove(websocket)
        except ValueError:
            pass
        print(f"[WS/score] Client putus. Total: {len(state.score_clients)}")


from pathlib import Path
import os

LYRIC_DIR = Path(__file__).parent / "music" / "lyric"


def _find_lrc_file(song):
    """
    Mencari file LRC berdasarkan:
    1. lyric_file di database
    2. Artist - Title.lrc
    3. Title.lrc
    """

    # jika database sudah menyimpan nama file
    if getattr(song, "lyric_file", None):
        p = LYRIC_DIR / song.lyric_file
        if p.exists():
            return p

    # format Artist - Title.lrc
    filename = f"{song.artist} - {song.title}.lrc"
    p = LYRIC_DIR / filename
    if p.exists():
        return p

    # format Title.lrc
    filename = f"{song.title}.lrc"
    p = LYRIC_DIR / filename
    if p.exists():
        return p

    # scan semua file jika nama tidak persis sama
    if LYRIC_DIR.exists():
        title = song.title.lower()
        artist = song.artist.lower()

        for f in LYRIC_DIR.glob("*.lrc"):
            name = f.stem.lower()
            if title in name or artist in name:
                return f

    return None

# ─────────────────────────────────────────────
#  REST API — Songs
# ─────────────────────────────────────────────
@app.get("/api/songs")
def get_songs(db: DBSession = Depends(get_db)):
    """Kembalikan daftar lagu dari database."""
    songs = db.query(Song).all()
    result = []
    for s in songs:
        # Cek keberadaan file audio & video
        audio_path = os.path.join(os.path.dirname(__file__), s.audio_file) if s.audio_file else None
        video_path = os.path.join(os.path.dirname(__file__), s.video_file) if s.video_file else None
        
        has_audio  = audio_path is not None and os.path.isfile(audio_path)
        has_video  = video_path is not None and os.path.isfile(video_path)

        # Cek lirik — scan folder lyric untuk kecocokan judul/artis
        has_lyrics = _find_lrc_file(s) is not None

        result.append({
            "id":           s.id,
            "title":        s.title,
            "artist":       s.artist,
            "genre":        s.genre,
            "duration_sec": s.duration_sec,
            "play_count":   s.play_count,
            "year":         s.year,
            "audio_file":   s.audio_file,
            "video_file":   s.video_file,
            "lyric_file":   s.lyric_file,
            "has_audio":    has_audio,
            "has_video":    has_video,
            "has_lyrics":   has_lyrics,
        })
    return {"status": "ok", "data": result}


# ─────────────────────────────────────────────
#  REST API — Lyrics
# ─────────────────────────────────────────────
@app.get("/api/lyrics/{song_id}")
def get_lyrics(song_id: int, db: DBSession = Depends(get_db)):
    """Kembalikan konten file LRC untuk lagu."""
    song = db.query(Song).filter(Song.id == song_id).first()
    if not song:
        return {"lyrics": None, "error": "Lagu tidak ditemukan"}
    lrc_path = _find_lrc_file(song)
    if lrc_path is None:
        return {"lyrics": None}
    try:
        with open(lrc_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return {"lyrics": content, "format": "lrc"}
    except Exception as e:
        return {"lyrics": None, "error": str(e)}


# ─────────────────────────────────────────────
#  REST API — Session Management
# ─────────────────────────────────────────────
@app.post("/api/session/start")
def start_session(body: dict, db: DBSession = Depends(get_db)):
    """Mulai sesi karaoke baru."""
    song_id = body.get("song_id", 1)
    user_id = body.get("user_id", 1)

    # Reset state skor
    state.accumulated_score = 0.0
    state.total_frames      = 0
    state.scored_frames     = 0
    state.session_start_ms  = int(datetime.now().timestamp() * 1000)
    state.song_pitch_ref    = []

    # Load pitch reference dari DB
    if song_id < 100:
        song = db.query(Song).filter(Song.id == song_id).first()
        if song and song.pitch_ref:
            try:
                raw = json.loads(song.pitch_ref)
                state.song_pitch_ref = [(ev["t"], ev["hz"]) for ev in raw]
            except Exception:
                pass

        # Buat record sesi di database
        new_session = KaraokeSession(
            user_id=user_id,
            song_id=song_id,
            started_at=datetime.utcnow(),
            status="active",
        )
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        state.session_id = new_session.id
    else:
        # Lagu built-in tidak disimpan ke DB
        state.session_id = -song_id

    print(f"[Session] Mulai — song_id={song_id}, session_id={state.session_id}")
    return {
        "status": "ok",
        "session_id": state.session_id,
        "song_id": song_id,
    }


@app.post("/api/session/end")
def end_session(body: dict, db: DBSession = Depends(get_db)):
    """Akhiri sesi dan simpan skor final."""
    session_id = body.get("session_id", state.session_id)

    final_score = 0.0
    if state.scored_frames > 0:
        final_score = state.accumulated_score / state.scored_frames

    grade, label = get_grade(final_score)

    # Simpan ke DB jika sesi bukan built-in
    if session_id and session_id > 0:
        sess = db.query(KaraokeSession).filter(
            KaraokeSession.id == session_id
        ).first()
        if sess:
            sess.ended_at   = datetime.utcnow()
            sess.final_score = round(final_score, 2)
            sess.status     = "completed"
            db.commit()

    # Update play_count lagu
    song_id = body.get("song_id")
    if song_id and song_id < 100:
        song = db.query(Song).filter(Song.id == song_id).first()
        if song:
            song.play_count = (song.play_count or 0) + 1
            db.commit()

    # Reset state
    state.session_id    = None
    state.session_start_ms = None

    print(f"[Session] Selesai — skor={final_score:.1f}, grade={grade}")
    return {
        "status":       "ok",
        "final_score":  round(final_score, 1),
        "grade":        grade,
        "label":        label,
        "total_frames": state.total_frames,
        "scored_frames": state.scored_frames,
    }


# ─────────────────────────────────────────────
#  REST API — Leaderboard / Riwayat
# ─────────────────────────────────────────────
@app.get("/api/sessions")
def get_sessions(db: DBSession = Depends(get_db)):
    """Kembalikan riwayat sesi (leaderboard)."""
    rows = (
        db.query(KaraokeSession)
        .filter(KaraokeSession.status == "completed")
        .order_by(KaraokeSession.final_score.desc())
        .limit(20)
        .all()
    )
    result = []
    for r in rows:
        song = db.query(Song).filter(Song.id == r.song_id).first()
        user = db.query(User).filter(User.id == r.user_id).first()
        grade, _ = get_grade(r.final_score or 0)
        result.append({
            "id":          r.id,
            "song_title":  song.title if song else "?",
            "user_name":   user.name if user else "?",
            "final_score": r.final_score,
            "grade":       grade,
            "date":        r.started_at.isoformat() if r.started_at else "",
        })
    return {"status": "ok", "data": result}


# ─────────────────────────────────────────────
#  REST API — Status (untuk monitoring)
# ─────────────────────────────────────────────
@app.get("/api/status")
def get_status():
    """Status server — berguna untuk monitoring dan demo Wireshark."""
    return {
        "status":           "ok",
        "udp_port":         UDP_PORT,
        "sep_available":    HAS_SEP,
        "sep_mode":         state.sep_mode,
        "active_session":   state.session_id,
        "score_clients":    len(state.score_clients),
        "packets_received": state.packets_received,
        "bytes_received":   state.bytes_received,
        "last_pitch_hz":    round(state.last_pitch_hz, 2),
        "scored_frames":    state.scored_frames,
        "total_frames":     state.total_frames,
    }


# ─────────────────────────────────────────────
#  BACKWARD COMPAT — daftar video lama
# ─────────────────────────────────────────────
@app.get("/api/videos")
def get_videos():
    """Endpoint lama — kembalikan daftar file MP4 di folder media."""
    try:
        files = [f for f in os.listdir(MEDIA_DIR) if f.endswith(".mp4")]
        return {"status": "sukses", "data": files}
    except Exception as e:
        return {"status": "error", "pesan": str(e)}


print("=" * 55)
print("  Karaoke Online Server v2.0")
print(f"  HTTP/WS  : http://127.0.0.1:8001")
print(f"  UDP Audio: udp://127.0.0.1:{UDP_PORT}")
print(f"  VoiceSep : {'Aktif' if HAS_SEP else 'Tidak tersedia'}")
print("=" * 55)