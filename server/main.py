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

from database import get_db, SessionLocal, Song, User, Session as KaraokeSession, PitchLog, NetworkStat
from pitch_utils import yin_pitch, freq_to_note, score_pitch, get_grade, get_pitch_label, PitchTracker
from pitch_ref_data import get_pitch_ref
from midi_utils import parse_midi_file, parse_midi_segments

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

    # Segmentasi suku kata (syllable-based scoring)
    song_midi_segments: list = []  # [{"idx", "start_ms", "end_ms", "hz", "note"}]
    current_syllable_idx: int = -1
    current_syllable_pitches: list = []  # Kumpulan hz user di segmen saat ini

    # WebSocket clients untuk streaming skor
    score_clients: List[WebSocket] = []

    # Statistik jaringan (untuk demo Wireshark)
    packets_received: int = 0
    bytes_received: int = 0
    last_pitch_hz: float = 0.0
    sep_mode: str = "raw"

state = AppState()
tracker = PitchTracker(sr=SAMPLE_RATE)

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


async def broadcast_to_clients(payload: dict):
    """Kirim data JSON ke semua WebSocket client yang terhubung."""
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


def evaluate_and_save_syllable(session_id: int, syllable_idx: int, pitches: list, ref_hz: float):
    """
    Evaluasi frekuensi suara user untuk satu segmen suku kata utuh (Cek Frekuensi).
    Skor dihitung sekali per suku kata dan disimpan ke database (PitchLog).
    Hasilnya di-broadcast ke frontend via WebSocket.
    """
    if not pitches:
        # User diam sepanjang suku kata -> MISS (skor 0.0)
        segment_score = 0.0
        median_hz = 0.0
    else:
        # Cek Frekuensi: Ambil median dari semua pitch deteksi vokal di segmen ini
        median_hz = float(np.median(pitches))
        segment_score = score_pitch(median_hz, ref_hz)

    # Simpan log ke database SQLite
    db = SessionLocal()
    try:
        cents_diff = None
        if ref_hz > 0 and median_hz > 0:
            # Selaraskan oktaf untuk menghitung cents_diff yang akurat
            octave_diff = round(np.log2(median_hz / ref_hz))
            median_hz_aligned = median_hz / (2.0 ** octave_diff)
            cents_diff = 1200.0 * np.log2(median_hz_aligned / ref_hz)

        note_info = freq_to_note(median_hz) if median_hz > 0 else None
        
        log = PitchLog(
            session_id=session_id,
            timestamp_ms=int(datetime.now().timestamp() * 1000),
            ref_hz=round(ref_hz, 2),
            user_hz=round(median_hz, 2),
            note=note_info["name"] if note_info else "--",
            is_match=segment_score >= 60.0,
            segment_score=round(segment_score, 1),
            syllable_idx=syllable_idx
        )
        db.add(log)
        db.commit()
        print(f"[Syllable Eval] Idx: {syllable_idx} | User: {log.user_hz}Hz | Ref: {log.ref_hz}Hz | Score: {log.segment_score}")
    except Exception as e:
        print(f"[Syllable Eval] Error DB: {e}")
    finally:
        db.close()

    # Akumulasikan skor ke state sesi karaoke
    state.accumulated_score += segment_score
    state.scored_frames += 1

    # Broadcast event evaluasi suku kata ke frontend
    payload = {
        "type": "syllable_eval",
        "idx": syllable_idx,
        "score": round(segment_score, 1),
        "user_hz": round(median_hz, 2),
        "ref_hz": round(ref_hz, 2),
        "label": get_pitch_label(cents_diff) if cents_diff is not None else "MISS",
        "note": note_info["name"] if note_info else "--",
    }
    
    # Bungkus dalam loop async luar (karena dipanggil dari background processor)
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(broadcast_to_clients(payload))


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

        # ── PitchTracker Detection (real-time smoothing) ───────────
        track_res = tracker.process(audio_f32)
        pitch_hz = track_res["freq"] if track_res["voiced"] else None
        note_info = track_res["note"]

        # ── Volume (RMS) ──────────────────────────────────────────
        rms = float(np.sqrt(np.mean(audio_f32 ** 2)))
        is_singing = rms > 0.008 and pitch_hz is not None

        # ── Segmentasi Suku Kata & Scoring ──────────────────────────
        frame_score = 0.0
        ref_hz = 0.0
        cents_diff = None

        state.total_frames += 1

        if state.session_id is not None:
            now_ms = int(datetime.now().timestamp() * 1000)
            elapsed_ms = now_ms - (state.session_start_ms or now_ms)

            # Cari suku kata/segmen MIDI yang mencakup waktu elapsed_ms
            curr_seg = None
            if state.song_midi_segments:
                for seg in state.song_midi_segments:
                    if seg["start_ms"] <= elapsed_ms <= seg["end_ms"]:
                        curr_seg = seg
                        break

            # Deteksi transisi / pergantian segmen suku kata
            curr_idx = curr_seg["idx"] if curr_seg else -1
            if curr_idx != state.current_syllable_idx:
                # Segmen suku kata sebelumnya selesai -> lakukan evaluasi akhir (Cek Frekuensi)
                if state.current_syllable_idx != -1:
                    prev_seg = next((s for s in state.song_midi_segments if s["idx"] == state.current_syllable_idx), None)
                    if prev_seg:
                        evaluate_and_save_syllable(
                            session_id=state.session_id,
                            syllable_idx=state.current_syllable_idx,
                            pitches=state.current_syllable_pitches,
                            ref_hz=prev_seg["hz"]
                        )
                # Mulai segmen suku kata baru
                state.current_syllable_idx = curr_idx
                state.current_syllable_pitches = []

            # Kumpulkan pitch user selama bernyanyi dalam segmen aktif
            if is_singing and state.current_syllable_idx != -1:
                state.current_syllable_pitches.append(pitch_hz)

            # Hitung ref_hz & frame_score real-time untuk visual feedback tracker/meter
            if curr_seg:
                ref_hz = curr_seg["hz"]
                if pitch_hz and ref_hz > 0:
                    # Selaraskan oktaf untuk menghitung frame_score & cents_diff instan
                    octave_diff = round(np.log2(pitch_hz / ref_hz))
                    pitch_hz_aligned = pitch_hz / (2.0 ** octave_diff)
                    cents_diff = 1200.0 * np.log2(pitch_hz_aligned / ref_hz)
                    frame_score = score_pitch(pitch_hz, ref_hz)

        # ── Running average skor (rata-rata dari segmen suku kata yang selesai) ──
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
MIDI_DIR = Path(__file__).parent / "music" / "media" / "midi"


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


def _find_midi_file(song):
    """
    Mencari file MIDI berdasarkan:
    1. pitch_ref di database jika berupa file .mid atau .midi
    2. Artist - Title.mid atau .midi
    3. Title.mid atau .midi
    4. Substring pencarian di folder midi
    """
    if not MIDI_DIR.exists():
        return None

    # 1. Jika database menyimpan nama file MIDI di kolom pitch_ref
    if getattr(song, "pitch_ref", None):
        ref_str = song.pitch_ref.strip()
        if ref_str.lower().endswith(('.mid', '.midi')):
            p = MIDI_DIR / ref_str
            if p.exists():
                return p

    # 2. format Artist - Title.mid / .midi
    for ext in ['.mid', '.midi']:
        p = MIDI_DIR / f"{song.artist} - {song.title}{ext}"
        if p.exists():
            return p

    # 3. format Title.mid / .midi
    for ext in ['.mid', '.midi']:
        p = MIDI_DIR / f"{song.title}{ext}"
        if p.exists():
            return p

    # 4. scan semua file di folder midi
    title = song.title.lower()
    artist = song.artist.lower()
    for f in MIDI_DIR.glob("**/*"):
        if f.suffix.lower() in ['.mid', '.midi']:
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
#  REST API — Pitch Reference
# ─────────────────────────────────────────────
@app.get("/api/pitch-ref/{song_id}")
def get_pitch_reference(song_id: int, db: DBSession = Depends(get_db)):
    """
    Kembalikan data pitch reference (melodi vokal) untuk lagu tertentu.
    Prioritas:
    1. Ekstraksi otomatis dari file MIDI (.mid/.midi) di server/music/midi/
    2. Data curated dari pitch_ref_data.py
    3. Simpanan database song.pitch_ref
    """
    song = db.query(Song).filter(Song.id == song_id).first()
    
    # 1. Coba cari dan parse file MIDI dinamis
    if song:
        midi_path = _find_midi_file(song)
        if midi_path:
            midi_data = parse_midi_file(str(midi_path))
            if midi_data:
                return {
                    "status": "ok", 
                    "song_id": song_id, 
                    "data": midi_data, 
                    "source": "midi", 
                    "file": midi_path.name
                }

    # 2. Coba dari pitch_ref_data.py (curated)
    curated = get_pitch_ref(song_id)
    if curated:
        return {"status": "ok", "song_id": song_id, "data": curated, "source": "curated"}

    # 3. Fallback: ambil dari database
    if not song:
        return {"status": "error", "data": [], "error": "Lagu tidak ditemukan"}

    if song.pitch_ref:
        try:
            raw = json.loads(song.pitch_ref)
            # Normalise: pastikan ada field 'note'
            data = []
            for ev in raw:
                hz = ev.get("hz", 0)
                note_info = freq_to_note(hz) if hz and hz > 0 else None
                data.append({
                    "t":    ev.get("t", 0),
                    "hz":   hz,
                    "note": note_info["name"] if note_info else "",
                })
            return {"status": "ok", "song_id": song_id, "data": data, "source": "database"}
        except Exception as e:
            return {"status": "error", "data": [], "error": str(e)}

    return {"status": "ok", "song_id": song_id, "data": [], "source": "none"}


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
    
    # Reset segmentasi suku kata
    state.song_midi_segments = []
    state.current_syllable_idx = -1
    state.current_syllable_pitches = []
    tracker.reset()

    # Load pitch reference dari MIDI, curated, atau DB
    midi_loaded = False
    if song_id < 100:
        song = db.query(Song).filter(Song.id == song_id).first()
        if song:
            # 1. Coba MIDI dinamis
            midi_path = _find_midi_file(song)
            if midi_path:
                midi_data = parse_midi_file(str(midi_path))
                if midi_data:
                    state.song_pitch_ref = [(ev["t"], ev["hz"]) for ev in midi_data]
                    # Load segmen suku kata dari MIDI
                    state.song_midi_segments = parse_midi_segments(str(midi_path))
                    midi_loaded = True
                    print(f"[Session] Pitch ref & {len(state.song_midi_segments)} segmen di-load dari MIDI: {midi_path.name}")

            # 2. Coba curated
            if not midi_loaded:
                curated = get_pitch_ref(song_id)
                if curated:
                    state.song_pitch_ref = [(ev["t"], ev["hz"]) for ev in curated]
                    # Buat segmen suku kata tiruan dari data curated dengan mengelompokkan waktu
                    segments = []
                    prev_hz = -1.0
                    start_t = 0
                    idx = 0
                    for ev in curated:
                        hz = ev["hz"]
                        if hz != prev_hz:
                            if prev_hz > 0:
                                segments.append({
                                    "idx": idx,
                                    "start_ms": start_t,
                                    "end_ms": ev["t"],
                                    "hz": prev_hz,
                                    "note": ev.get("note", "")
                                })
                                idx += 1
                            start_t = ev["t"]
                            prev_hz = hz
                    state.song_midi_segments = segments
                    midi_loaded = True
                    print(f"[Session] Pitch ref & {len(segments)} segmen di-load dari curated data")

            # 3. Fallback database
            if not midi_loaded and song.pitch_ref:
                try:
                    raw = json.loads(song.pitch_ref)
                    state.song_pitch_ref = [(ev["t"], ev["hz"]) for ev in raw]
                    print(f"[Session] Pitch ref di-load dari database")
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

    # Siapkan preview pitch ref (100 titik pertama) untuk frontend
    pitch_preview = get_pitch_ref(song_id)
    if not pitch_preview and state.song_pitch_ref:
        pitch_preview = [{"t": t, "hz": hz, "note": ""} for t, hz in state.song_pitch_ref[:100]]

    print(f"[Session] Mulai — song_id={song_id}, session_id={state.session_id}, ref_points={len(pitch_preview)}")
    return {
        "status":       "ok",
        "session_id":   state.session_id,
        "song_id":      song_id,
        "has_pitch_ref": len(pitch_preview) > 0,
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