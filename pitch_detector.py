"""
=============================================================
  KARAOKE PITCH DETECTOR
  Modul: Live Pitch Detection + MIDI Reference + Karaoke Score

  Algoritma Pitch  : YIN (de Cheveigné & Kawahara, 2002)
  Segmentasi       : Onset detection via amplitude envelope
  Reference Pitch  : MIDI file parsing (vocal track only)
  Scoring          : Cents-based frame accuracy + session grade
  Visualisasi      : Matplotlib live dashboard (FuncAnimation)

  Lagu Built-in (public domain):
    1. Twinkle Twinkle Little Star
    2. Ode to Joy — Beethoven
    3. Happy Birthday to You
  Atau load file MIDI eksternal via argumen --midi
=============================================================
"""

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.gridspec as gridspec
import threading
import queue
import time
import collections
import io
import base64
import argparse
import os
import mido

# ─────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────
SAMPLE_RATE    = 44100
CHUNK_SIZE     = 2048      # ~46ms per frame
CHANNELS       = 1

# YIN Algorithm
YIN_THRESHOLD  = 0.15
FREQ_MIN       = 70
FREQ_MAX       = 1100

# Onset / Syllable
ONSET_THRESH   = 0.030
ONSET_COOLDOWN = 12
SILENCE_GAP    = 8

# Visualisasi
GRAPH_HISTORY  = 300
REFRESH_MS     = 50

# Scoring
SCORE_PERFECT_CENTS = 25   # ±25¢  → 100–80
SCORE_GOOD_CENTS    = 50   # ±50¢  → 80–50
SCORE_OK_CENTS      = 100  # ±100¢ → 50–0
# Toleransi sinkronisasi pitch vs reference (detik)
SYNC_WINDOW_SEC     = 0.25

# ─────────────────────────────────────────────
#  MIDI DATA BUILT-IN (base64-encoded)
# ─────────────────────────────────────────────
TWINKLE_B64 = (
    'TVRoZAAAAAYAAQABAeBNVHJrAAABjgD/UQMJJ8AA/wMFVm9jYWwAkDxagXCAPAAAkDxagXCAPA'
    'AAkENagXCAQwAAkENagXCAQwAAkEVagXCARQAAkEVagXCARQAAkENag2CAQwAAkEFagXCAQQAA'
    'kEFagXCAQQAAkEBagXCAQAAAkEBagXCAQAAAkD5agXCAPgAAkD5agXCAPgAAkDxag2CAPAAAkE'
    'NagXCAQwAAkENagXCAQwAAkEFagXCAQQAAkEFagXCAQQAAkEBagXCAQAAAkEBagXCAQAAAkD5a'
    'g2CAPgAAkENagXCAQwAAkENagXCAQwAAkEFagXCAQQAAkEFagXCAQQAAkEBagXCAQAAAkEBagX'
    'CAQAAAkD5ag2CAPgAAkDxagXCAPAAAkDxagXCAPAAAkENagXCAQwAAkENagXCAQwAAkEVagXCA'
    'RQAAkEVagXCARQAAkENag2CAQwAAkEFagXCAQQAAkEFagXCAQQAAkEBagXCAQAAAkEBagXCAPg'
    'AAkD5agXCAPgAAkD5agXCAPgAAkDxah0CAPAAA/y8A'
)

ODE_TO_JOY_B64 = (
    'TVRoZAAAAAYAAQABAeBNVHJrAAACOwD/UQMIeiQA/wMFVm9jYWwAkEBagXCAQAAAkEBagXCAPg'
    'AAkEFagXCAQQAAkENagXCAQwAAkENagXCAQwAAkEFagXCAQQAAkEBagXCAQAAAkD5agXCAPgAA'
    'kDxagXCAPAAAkDxagXCAPAAAkD5agXCAPgAAkEBagXCAQAAAkEBagmiAQAAAkD5aeIA+AACQPl'
    'qDYIA+AACQQFqBcIBAAACQQFqBcIBAAACQQVqBcIBBAACQQ1qBcIBDAACQQ1qBcIBDAACQQVqB'
    'cIBBAACQQFqBcIBAAACQPlqBcIA+AACQPFqBcIA8AACQPFqBcIA8AACQPlqBcIA+AACQQFqBcI'
    'BAAACQPlqCaIA+AACQPFp4gDwAAJA8WoNggDwAAJA+WoFwgD4AAJA+WoFwgD4AAJBAWoFwgEAA'
    'AJA8WoFwgDwAAJA+WoFwgD4AAJBAWniAQAAAkEFaeIBBAACQQFqBcIBAAACQPlqBcIA+AACQPF'
    'qBcIA8AACQPlqBcIA+AACQQFp4gEAAAJBBWniAQQAAkEBagXCAQAAAkD5agXCAPgAAkDxagXCA'
    'PAAAkD5agXCAPgAAkDdag2CANwAAkEBagXCAQAAAkEBagXCAQAAAkEFagXCAQQAAkENagXCAQwAA'
    'kENagXCAQwAAkEFagXCAQQAAkEBagXCAQAAAkD5agXCAPgAAkDxagXCAPAAAkDxagXCAPAAAkD5'
    'agXCAPgAAkEBagXCAQAAAkD5agmiAPgAAkDxaeIA8AACQPFqHQIA8AAD/LwA='
)

HAPPY_BIRTHDAY_B64 = (
    'TVRoZAAAAAYAAQABAeBNVHJrAAAA8QD/UQMKLCsA/wMFVm9jYWwAkDdagTSANwAAkDdaPIA3AA'
    'CQOVqBcIA5AACQN1qBcIA3AACQPFqBcIA8AACQO1qDYIA7AACQN1qBNIA3AACQN1o8gDcAAJA5'
    'WoFwgDkAAJA3WoFwgDcAAJA+WoFwgD4AAJA8WoNggDwAAJA3WoE0gDcAAJA3WjyANwAAkENagX'
    'CAQwAAkEBagXCAQAAAkDxagXCAPAAAkDtagXCAOwAAkDlag2CAOQAAkEFagTSAQQAAkEFaPIBBAA'
    'CQQFqBcIBAAACQPFqBcIA8AACQPlqBcIA+AACQPFqFUIA8AAD/LwA='
)

BUILTIN_SONGS = {
    "1": {
        "name": "Twinkle Twinkle Little Star",
        "artist": "Traditional (Public Domain)",
        "b64": TWINKLE_B64,
        "bpm": 100,
    },
    "2": {
        "name": "Ode to Joy",
        "artist": "L.V. Beethoven (Public Domain)",
        "b64": ODE_TO_JOY_B64,
        "bpm": 108,
    },
    "3": {
        "name": "Happy Birthday to You",
        "artist": "Traditional (Public Domain)",
        "b64": HAPPY_BIRTHDAY_B64,
        "bpm": 90,
    },
}

# ─────────────────────────────────────────────
#  NOTE MAPPING
# ─────────────────────────────────────────────

NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
# Daftar nama nada dalam satu oktaf.

REFERENCE_FREQ_LINES = [
    (82.4,'E2'),(130.8,'C3'),(196.0,'G3'),
    (261.6,'C4'),(329.6,'E4'),(392.0,'G4'),
    (523.3,'C5'),(659.3,'E5'),(783.9,'G5'),
]
# Frekuensi referensi yang ditampilkan pada grafik.


def freq_to_note(freq: float) -> dict | None:
    # Mengubah frekuensi (Hz) menjadi informasi nada musik.

    if freq is None or freq <= 0:
        # Memeriksa apakah frekuensi valid.
        return None

    semitones = 12 * np.log2(freq / 440.0) + 69
    # Menghitung posisi nada dalam skala MIDI.

    midi_note = int(round(semitones))
    # Membulatkan ke nomor MIDI terdekat.

    name = NOTE_NAMES[midi_note % 12]
    # Mengambil nama nada.

    octave = (midi_note // 12) - 1
    # Menghitung nomor oktaf.

    cents = round((semitones - midi_note) * 100)
    # Menghitung deviasi nada dalam cents.

    return {"name": f"{name}{octave}", "note": name, "octave": octave, "cents": cents}
    # Mengembalikan informasi nada.


def midi_note_to_hz(midi_note: int) -> float:
    # Mengubah nomor MIDI menjadi frekuensi (Hz).

    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))
    # Menghitung frekuensi berdasarkan nomor MIDI.


# ─────────────────────────────────────────────
#  MIDI REFERENCE LOADER
# ─────────────────────────────────────────────
class MidiReference:
    """
    Parse file MIDI dan ekstrak timeline pitch vokal.

    Output: list of (start_sec, end_sec, freq_hz) events,
            total_duration_sec
    """

    def __init__(self):
        self.events: list[tuple[float, float, float]] = []
        # Menyimpan daftar event nada dalam format (mulai, selesai, frekuensi).

        self.total_duration: float = 0.0
        # Menyimpan total durasi lagu.

        self.song_name: str = "—"
        # Menyimpan nama lagu.

        self.artist: str = "—"
        # Menyimpan nama artis.

        self.bpm: float = 120.0
        # Menyimpan tempo lagu dalam BPM.

    def load_from_bytes(self, midi_bytes: bytes, song_name: str = "Song",
                        artist: str = "Unknown"):
        self.song_name = song_name
        # Menyimpan nama lagu.

        self.artist = artist
        # Menyimpan nama artis.

        mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
        # Membuat objek MIDI dari data byte.

        self._parse(mid)
        # Memproses isi file MIDI.

    def load_from_file(self, path: str):
        self.song_name = os.path.splitext(os.path.basename(path))[0]
        # Mengambil nama file sebagai nama lagu.

        self.artist = "External MIDI"
        # Memberi label bahwa MIDI berasal dari file eksternal.

        mid = mido.MidiFile(path)
        # Membaca file MIDI dari disk.

        self._parse(mid)
        # Memproses isi file MIDI.

    def _parse(self, mid: mido.MidiFile):
        """
        Konversi pesan MIDI note_on/note_off → (start, end, Hz) events.
        """

        tpb = mid.ticks_per_beat
        # Mengambil jumlah tick per beat dari file MIDI.

        tempo_map = []
        # Menyimpan perubahan tempo.

        abs_tick = 0
        # Menyimpan posisi tick absolut.

        running_tempo = 500000
        # Tempo default MIDI (120 BPM).

        for track in mid.tracks:
            # Memeriksa setiap track MIDI.

            abs_tick = 0
            # Reset posisi tick untuk track baru.

            for msg in track:
                # Membaca setiap pesan MIDI.

                abs_tick += msg.time
                # Menambahkan delta time ke tick absolut.

                if msg.type == 'set_tempo':
                    # Jika ditemukan perubahan tempo.

                    tempo_map.append((abs_tick, msg.tempo))
                    # Menyimpan posisi dan nilai tempo.

        if not tempo_map:
            # Jika tidak ada tempo dalam file.

            tempo_map = [(0, running_tempo)]
            # Gunakan tempo default.

        tempo_map.sort(key=lambda x: x[0])
        # Mengurutkan tempo berdasarkan posisi tick.

        self.bpm = round(mido.tempo2bpm(tempo_map[-1][1]), 1)
        # Menghitung BPM dari tempo terakhir.

        def ticks_to_sec(abs_ticks: int) -> float:
            """Konversi absolute ticks ke detik dengan tempo map."""

            sec = 0.0
            # Menyimpan hasil waktu dalam detik.

            prev_tick = 0
            # Menyimpan tick sebelumnya.

            prev_tempo = tempo_map[0][1]
            # Menyimpan tempo sebelumnya.

            for tm_tick, tm_tempo in tempo_map:
                # Menelusuri setiap perubahan tempo.

                if abs_ticks <= tm_tick:
                    break
                    # Berhenti jika target tick sudah tercapai.

                span = min(abs_ticks, tm_tick) - prev_tick
                # Menghitung panjang segmen tick.

                sec += span * prev_tempo / (tpb * 1e6)
                # Mengonversi tick menjadi detik.

                prev_tick = tm_tick
                # Memperbarui tick sebelumnya.

                prev_tempo = tm_tempo
                # Memperbarui tempo sebelumnya.

            sec += (abs_ticks - prev_tick) * prev_tempo / (tpb * 1e6)
            # Menghitung sisa waktu setelah tempo terakhir.

            return sec
            # Mengembalikan waktu dalam detik.

        best_track = None
        # Menyimpan kandidat track terbaik.

        best_score = -1
        # Menyimpan jumlah note terbanyak.

        keyword_tracks = []
        # Menyimpan track yang mengandung kata kunci vokal.

        for track in mid.tracks:
            # Memeriksa semua track.

            name_lower = track.name.lower()
            # Mengubah nama track menjadi huruf kecil.

            has_keyword = any(
                k in name_lower
                for k in ['vocal','voice','melody','lead','sing','vox']
            )
            # Mengecek apakah nama track mengandung kata kunci vokal.

            note_count = sum(
                1 for m in track
                if m.type == 'note_on'
                and m.velocity > 0
                and getattr(m, 'channel', 0) != 9
            )
            # Menghitung jumlah note aktif selain channel drum.

            if has_keyword and note_count > 0:
                keyword_tracks.append((track, note_count))
                # Menyimpan track yang cocok dengan kata kunci.

            if note_count > best_score:
                best_score = note_count
                # Memperbarui skor terbaik.

                best_track = track
                # Menyimpan track terbaik sementara.

        selected = keyword_tracks[0][0] if keyword_tracks else best_track
        # Memilih track vokal atau track dengan note terbanyak.

        if selected is None:
            # Jika tidak ada track yang cocok.

            print("[MidiReference] Peringatan: tidak ada track yang cocok.")
            return

        print(f"[MidiReference] Track terpilih: '{selected.name}' "
              f"({'keyword match' if keyword_tracks else 'most notes'})")
        # Menampilkan track yang dipilih.

        events = []
        # Menyimpan hasil event nada.

        active_notes = {}
        # Menyimpan note yang sedang aktif.

        abs_tick = 0
        # Posisi tick saat ini.

        for msg in selected:
            # Membaca pesan MIDI pada track terpilih.

            abs_tick += msg.time
            # Menambahkan waktu pesan ke tick absolut.

            if msg.type == 'note_on' and msg.velocity > 0 and getattr(msg,'channel',0) != 9:
                active_notes[msg.note] = abs_tick
                # Menyimpan waktu mulai note.

            elif msg.type in ('note_off', 'note_on') and \
                 (msg.type == 'note_off' or msg.velocity == 0):

                note = msg.note
                # Mengambil nomor note.

                if note in active_notes:
                    # Jika note sebelumnya aktif.

                    start_t = ticks_to_sec(active_notes.pop(note))
                    # Menghitung waktu mulai note.

                    end_t = ticks_to_sec(abs_tick)
                    # Menghitung waktu selesai note.

                    if end_t > start_t:
                        # Memastikan durasi valid.

                        events.append(
                            (start_t, end_t, midi_note_to_hz(note))
                        )
                        # Menambahkan event ke daftar.

        for note, start_tick in active_notes.items():
            # Menangani note yang belum ditutup.

            start_t = ticks_to_sec(start_tick)
            # Menghitung waktu mulai.

            end_t = ticks_to_sec(abs_tick + 1)
            # Menghitung waktu selesai sementara.

            events.append(
                (start_t, end_t, midi_note_to_hz(note))
            )
            # Menambahkan event ke daftar.

        self.events = sorted(events, key=lambda x: x[0])
        # Mengurutkan event berdasarkan waktu mulai.

        self.total_duration = max(
            (e[1] for e in self.events),
            default=0.0
        )
        # Menghitung total durasi lagu.

        print(
            f"[MidiReference] {len(self.events)} note events, "
            f"durasi {self.total_duration:.1f}s, BPM ~{self.bpm}"
        )
        # Menampilkan ringkasan hasil parsing.

    def get_ref_freq_at(self, t: float) -> float | None:
        """Kembalikan frekuensi referensi pada waktu t (detik)."""

        for start, end, freq in self.events:
            # Memeriksa seluruh event nada.

            if start <= t < end:
                # Jika waktu t berada dalam rentang event.

                return freq
                # Mengembalikan frekuensi referensi.

        return None
        # Tidak ada nada aktif pada waktu tersebut.

    def get_upcoming_events(self, t: float, window: float = 5.0) \
            -> list[tuple[float, float, float]]:
        """Ambil event yang akan datang dalam window detik ke depan."""

        return [
            (s, e, f)
            for s, e, f in self.events
            if s >= t and s <= t + window
        ]
        # Mengembalikan daftar event yang akan dimainkan dalam rentang waktu tertentu.


# ─────────────────────────────────────────────
#  KARAOKE SCORE ENGINE
# ─────────────────────────────────────────────

def cents_error(user_hz: float, ref_hz: float) -> float | None:
    """Menghitung selisih pitch antara user dan referensi dalam satuan cents."""

    if not user_hz or not ref_hz or user_hz <= 0 or ref_hz <= 0:
        # Memastikan kedua frekuensi valid.
        return None

    return 1200.0 * np.log2(user_hz / ref_hz)
    # Menghitung selisih pitch dalam cents.


def frame_score(cents_err: float | None) -> float:
    """
    Mengubah selisih pitch menjadi skor 0–100.
    """

    if cents_err is None:
        # Tidak ada pitch yang dapat dinilai.
        return None

    e = abs(cents_err)
    # Mengambil nilai absolut error.

    if e <= SCORE_PERFECT_CENTS:
        return 100.0 - e * 0.8
        # Menghitung skor untuk pitch yang sangat akurat.

    elif e <= SCORE_GOOD_CENTS:
        return 80.0 - (e - SCORE_PERFECT_CENTS) * 1.2
        # Menghitung skor untuk pitch yang masih baik.

    elif e <= SCORE_OK_CENTS:
        return 50.0 - (e - SCORE_GOOD_CENTS) * 1.0
        # Menghitung skor untuk pitch yang cukup mendekati.

    return 0.0
    # Pitch dianggap salah.


def score_to_grade(score: float) -> tuple[str, str]:
    """Mengubah skor menjadi grade dan warna tampilan."""

    if score >= 95: return "PERFECT ★",  '#ffd700'
    # Grade tertinggi.

    if score >= 85: return "GREAT ✦",    '#00d4aa'
    # Grade sangat baik.

    if score >= 70: return "GOOD ✓",     '#7c6fff'
    # Grade baik.

    if score >= 55: return "OK ~",       '#ffb84d'
    # Grade cukup.

    if score >= 35: return "TRY AGAIN ↺",'#ff8c42'
    # Grade kurang.

    return             "MISS ✗",         '#ff6b6b'
    # Grade gagal.


class KaraokeScorer:
    """
    Mengelola perhitungan skor karaoke secara real-time.
    """

    def __init__(self):
        self.frame_scores: list[float] = []
        # Menyimpan skor setiap frame.

        self.cents_history: list[float | None] = []
        # Menyimpan riwayat error pitch.

        self.ref_hz_history: list[float | None] = []
        # Menyimpan riwayat frekuensi referensi.

        self._total_ref_frames = 0
        # Menghitung jumlah frame dengan referensi aktif.

        self._total_good_frames = 0
        # Menghitung jumlah frame dengan skor baik.

    def update(self, user_hz: float | None, ref_hz: float | None) -> dict:
        """
        Memproses satu frame dan memperbarui statistik skor.
        """

        err = None
        # Menyimpan error pitch.

        sc = None
        # Menyimpan skor frame.

        if ref_hz is not None:
            # Hanya menghitung skor jika ada nada referensi.

            self._total_ref_frames += 1
            # Menambah jumlah frame referensi.

            if user_hz and user_hz > 0:
                # Jika user menghasilkan suara.

                err = cents_error(user_hz, ref_hz)
                # Menghitung error pitch.

                sc = frame_score(err)
                # Menghitung skor frame.

                self.frame_scores.append(sc)
                # Menyimpan skor frame.

                if sc >= 50:
                    self._total_good_frames += 1
                    # Menambah jumlah frame yang dianggap benar.

            else:
                # Jika user diam saat referensi aktif.

                sc = 0.0
                # Memberikan skor nol.

                self.frame_scores.append(0.0)
                # Menyimpan skor nol.

        self.cents_history.append(err)
        # Menyimpan error ke riwayat.

        self.ref_hz_history.append(ref_hz)
        # Menyimpan referensi ke riwayat.

        session_score = self.get_session_score()
        # Menghitung skor keseluruhan sesi.

        grade, grade_color = score_to_grade(session_score)
        # Mengubah skor menjadi grade.

        return {
            "frame_score": sc,
            # Skor frame saat ini.

            "cents_err": err,
            # Error pitch saat ini.

            "session_score": session_score,
            # Skor keseluruhan sesi.

            "grade": grade,
            # Grade saat ini.

            "grade_color": grade_color,
            # Warna grade.

            "ref_frames": self._total_ref_frames,
            # Total frame referensi.

            "good_frames": self._total_good_frames,
            # Total frame dengan skor baik.

            "accuracy_pct":
                (self._total_good_frames /
                 max(1, self._total_ref_frames)) * 100,
            # Persentase akurasi bernyanyi.
        }

    def get_session_score(self) -> float:
        # Menghitung skor sesi karaoke.

        if not self.frame_scores:
            # Jika belum ada skor.
            return 0.0

        arr = np.array(self.frame_scores[-200:])
        # Mengambil maksimal 200 skor terakhir.

        weights = np.exp(np.linspace(-1.5, 0, len(arr)))
        # Membuat bobot EMA.

        return float(np.average(arr, weights=weights))
        # Mengembalikan rata-rata berbobot.

    def get_recent_cents(self, n: int = GRAPH_HISTORY) -> list[float | None]:
        # Mengambil riwayat error pitch terbaru.
        return self.cents_history[-n:]

    def get_recent_ref(self, n: int = GRAPH_HISTORY) -> list[float | None]:
        # Mengambil riwayat frekuensi referensi terbaru.
        return self.ref_hz_history[-n:]


# ─────────────────────────────────────────────
#  YIN PITCH DETECTION
# ─────────────────────────────────────────────

def yin_pitch(audio_buffer: np.ndarray, sample_rate: int) -> float | None:
    # Mendeteksi pitch menggunakan algoritma YIN.

    N = len(audio_buffer)
    # Mengambil panjang buffer audio.

    tau_max = min(N // 2, int(sample_rate / FREQ_MIN))
    # Menentukan batas periode maksimum.

    tau_min = int(sample_rate / FREQ_MAX)
    # Menentukan batas periode minimum.

    diff = np.zeros(tau_max)
    # Menyiapkan array difference function.

    for tau in range(1, tau_max):
        # Menghitung difference function.

        segment = audio_buffer[:N - tau] - audio_buffer[tau:N]
        # Menghitung selisih sinyal yang digeser.

        diff[tau] = np.dot(segment, segment)
        # Menghitung energi perbedaan.

    cmn = np.ones(tau_max)
    # Menyiapkan CMND function.

    running_sum = 0.0
    # Menyimpan akumulasi difference.

    for tau in range(1, tau_max):
        # Menghitung CMND.

        running_sum += diff[tau]
        # Menambah nilai difference.

        cmn[tau] = diff[tau] * tau / running_sum if running_sum > 0 else 1.0
        # Melakukan normalisasi.

    tau_est = -1
    # Menyimpan kandidat periode pitch.

    for tau in range(tau_min, tau_max - 1):
        # Mencari kandidat pitch terbaik.

        if cmn[tau] < YIN_THRESHOLD:
            # Jika nilai melewati threshold.

            while tau + 1 < tau_max - 1 and cmn[tau + 1] < cmn[tau]:
                tau += 1
                # Mencari minimum lokal.

            tau_est = tau
            # Menyimpan estimasi periode.

            break

    if tau_est < 2:
        # Jika pitch tidak ditemukan.
        return None

    tau_b = tau_est - 1
    # Titik sebelum minimum.

    tau_c = min(tau_est + 1, tau_max - 1)
    # Titik setelah minimum.

    denom = 2.0 * (2.0 * cmn[tau_est] - cmn[tau_b] - cmn[tau_c])
    # Menghitung penyebut interpolasi parabola.

    tau_interp = tau_est if abs(denom) < 1e-9 else \
                 tau_est + (cmn[tau_c] - cmn[tau_b]) / denom
    # Memperhalus estimasi periode.

    return sample_rate / tau_interp
    # Mengubah periode menjadi frekuensi.


def compute_rms(audio_buffer: np.ndarray) -> float:
    # Menghitung RMS (energi rata-rata sinyal audio).

    return float(np.sqrt(np.mean(audio_buffer ** 2)))
    # Mengembalikan nilai RMS audio.


# ─────────────────────────────────────────────
#  SYLLABLE SEGMENTOR
# ─────────────────────────────────────────────

class SyllableSegmentor:
    def __init__(self):
        self.syllables: list[dict] = []
        # Menyimpan daftar suku kata yang telah terdeteksi.

        self._current_pitches: list[float] = []
        # Menyimpan pitch pada suku kata yang sedang diproses.

        self._current_start: int = 0
        # Menyimpan frame awal suku kata.

        self._frame_count: int = 0
        # Menyimpan jumlah frame yang telah diproses.

        self._silent_count: int = 0
        # Menghitung jumlah frame hening berturut-turut.

        self._cooldown: int = 0
        # Mencegah deteksi onset berulang dalam waktu singkat.

        self._prev_rms: float = 0.0
        # Menyimpan nilai RMS frame sebelumnya.

        self._in_voice: bool = False
        # Menandakan apakah sedang berada dalam segmen suara.

    def process(self, pitch: float | None, rms: float, frame_idx: int) -> dict | None:
        # Memproses satu frame audio untuk mendeteksi suku kata.

        is_voiced = rms > ONSET_THRESH
        # Menentukan apakah frame mengandung suara.

        rms_delta = rms - self._prev_rms
        # Menghitung perubahan RMS dari frame sebelumnya.

        completed = None
        # Menyimpan suku kata yang selesai diproses.

        if is_voiced:
            # Jika frame mengandung suara.

            self._silent_count = 0
            # Reset penghitung keheningan.

            onset = (not self._in_voice) or \
                    (self._cooldown <= 0 and rms_delta > ONSET_THRESH * 0.8)
            # Mendeteksi awal suku kata baru.

            if onset and self._in_voice and len(self._current_pitches) >= 3:
                completed = self._finalize_syllable(frame_idx - 1)
                # Menyelesaikan suku kata sebelumnya.

            if onset:
                self._current_start = frame_idx
                # Menyimpan frame awal suku kata.

                self._current_pitches = []
                # Mengosongkan daftar pitch sebelumnya.

                self._cooldown = ONSET_COOLDOWN
                # Mengaktifkan cooldown onset.

            if pitch and FREQ_MIN < pitch < FREQ_MAX:
                self._current_pitches.append(pitch)
                # Menambahkan pitch valid ke suku kata saat ini.

            self._in_voice = True
            # Menandakan sedang dalam segmen suara.

        else:
            # Jika frame tidak mengandung suara.

            if self._in_voice:
                # Jika sebelumnya sedang berbicara/bernyanyi.

                self._silent_count += 1
                # Menambah jumlah frame hening.

                if self._silent_count >= SILENCE_GAP and \
                   len(self._current_pitches) >= 3:

                    completed = self._finalize_syllable(frame_idx)
                    # Menyelesaikan suku kata yang sedang aktif.

                    self._in_voice = False
                    # Keluar dari mode suara.

                    self._silent_count = 0
                    # Reset penghitung keheningan.

        if self._cooldown > 0:
            self._cooldown -= 1
            # Mengurangi nilai cooldown.

        self._prev_rms = rms
        # Menyimpan RMS saat ini.

        self._frame_count = frame_idx
        # Menyimpan indeks frame terakhir.

        return completed
        # Mengembalikan suku kata yang selesai jika ada.

    def _finalize_syllable(self, end_idx: int) -> dict:
        # Membentuk informasi akhir suatu suku kata.

        pitches = [p for p in self._current_pitches if p]
        # Mengambil pitch yang valid.

        if pitches:
            avg_pitch = float(np.median(pitches))
            # Menghitung pitch rata-rata menggunakan median.

            min_pitch = float(np.min(pitches))
            # Mengambil pitch minimum.

            max_pitch = float(np.max(pitches))
            # Mengambil pitch maksimum.

            note_info = freq_to_note(avg_pitch)
            # Mengubah pitch rata-rata menjadi informasi nada.

        else:
            avg_pitch = min_pitch = max_pitch = 0.0
            # Mengisi nilai nol jika tidak ada pitch.

            note_info = None
            # Tidak ada informasi nada.

        syl = {
            "id": len(self.syllables) + 1,
            # ID suku kata.

            "start_idx": self._current_start,
            # Frame awal suku kata.

            "end_idx": end_idx,
            # Frame akhir suku kata.

            "duration": (end_idx - self._current_start) * CHUNK_SIZE / SAMPLE_RATE,
            # Durasi suku kata dalam detik.

            "avg_pitch": avg_pitch,
            # Pitch rata-rata.

            "min_pitch": min_pitch,
            # Pitch minimum.

            "max_pitch": max_pitch,
            # Pitch maksimum.

            "note": note_info,
            # Informasi nada hasil konversi.

            "pitches": pitches.copy(),
            # Salinan daftar pitch.
        }

        self.syllables.append(syl)
        # Menambahkan suku kata ke daftar hasil.

        self._current_pitches = []
        # Mengosongkan buffer pitch.

        return syl
        # Mengembalikan data suku kata.

# ─────────────────────────────────────────────
#  PITCH PROCESSOR
# ─────────────────────────────────────────────

class PitchProcessor:
    """
    Thread terpisah: deteksi pitch user + scoring vs MIDI reference.
    """

    def __init__(self, audio_queue: queue.Queue, midi_ref: MidiReference):
        self.audio_queue = audio_queue
        # Queue yang berisi data audio dari mikrofon.

        self.midi_ref = midi_ref
        # Objek referensi MIDI untuk mendapatkan nada target.

        self.scorer = KaraokeScorer()
        # Objek untuk menghitung skor karaoke.

        self.segmentor = SyllableSegmentor()
        # Objek untuk segmentasi suku kata.

        self.pitch_history = collections.deque(maxlen=GRAPH_HISTORY)
        # Menyimpan riwayat pitch terbaru.

        self.rms_history = collections.deque(maxlen=GRAPH_HISTORY)
        # Menyimpan riwayat RMS terbaru.

        self.waveform = np.zeros(CHUNK_SIZE)
        # Menyimpan waveform audio terakhir.

        self.current_pitch: float | None = None
        # Pitch yang sedang terdeteksi.

        self.current_rms: float = 0.0
        # RMS yang sedang terdeteksi.

        self.current_note: dict | None = None
        # Informasi nada yang sedang terdeteksi.

        self.current_score: dict = {}
        # Informasi skor saat ini.

        self.current_ref: float | None = None
        # Frekuensi referensi saat ini.

        self._frame_idx: int = 0
        # Indeks frame yang sedang diproses.

        self._running: bool = False
        # Status thread pemrosesan.

        self._thread: threading.Thread | None = None
        # Objek thread pemrosesan.

        self._lock = threading.Lock()
        # Lock untuk sinkronisasi data antar thread.

        self.new_syllable: dict | None = None
        # Menyimpan suku kata baru yang selesai diproses.

        self._start_time: float = 0.0
        # Waktu mulai karaoke.

        self._elapsed: float = 0.0
        # Waktu berjalan sejak karaoke dimulai.

        self.is_playing: bool = False
        # Status apakah karaoke sedang berjalan.

    def start(self):
        # Memulai thread pemrosesan pitch.

        self._running = True
        # Mengaktifkan status thread.

        self._start_time = time.time()
        # Menyimpan waktu mulai karaoke.

        self.is_playing = True
        # Menandakan karaoke sedang berjalan.

        self._thread = threading.Thread(target=self._run, daemon=True)
        # Membuat thread baru untuk pemrosesan audio.

        self._thread.start()
        # Menjalankan thread.

    def stop(self):
        # Menghentikan thread pemrosesan.

        self._running = False
        # Menonaktifkan loop pemrosesan.

        self.is_playing = False
        # Menandakan karaoke telah berhenti.

    def _run(self):
        # Loop utama pemrosesan audio.

        while self._running:
            # Berjalan selama sistem aktif.

            try:
                audio = self.audio_queue.get(timeout=0.1)
                # Mengambil buffer audio dari queue.

            except queue.Empty:
                continue
                # Lewati jika tidak ada data audio.

            rms = compute_rms(audio)
            # Menghitung RMS audio.

            pitch = yin_pitch(audio, SAMPLE_RATE)
            # Mendeteksi pitch menggunakan algoritma YIN.

            if pitch and len(self.pitch_history) > 10:
                # Melakukan filter outlier pitch.

                recent = [p for p in list(self.pitch_history)[-10:] if p]
                # Mengambil 10 pitch valid terakhir.

                if recent:
                    median = np.median(recent)
                    # Menghitung median pitch.

                    if abs(pitch - median) > median * 0.5:
                        pitch = None
                        # Mengabaikan pitch yang terlalu jauh dari median.

            elapsed = time.time() - self._start_time
            # Menghitung waktu berjalan karaoke.

            ref_hz = self.midi_ref.get_ref_freq_at(elapsed)
            # Mengambil frekuensi referensi MIDI pada waktu saat ini.

            score_info = self.scorer.update(pitch, ref_hz)
            # Menghitung skor berdasarkan pitch dan referensi.

            with self._lock:
                # Mengunci data agar aman dari akses bersamaan.

                self.current_pitch = pitch
                # Menyimpan pitch saat ini.

                self.current_rms = rms
                # Menyimpan RMS saat ini.

                self.current_note = freq_to_note(pitch) if pitch else None
                # Mengubah pitch menjadi informasi nada.

                self.current_score = score_info
                # Menyimpan informasi skor.

                self.current_ref = ref_hz
                # Menyimpan frekuensi referensi.

                self._elapsed = elapsed
                # Menyimpan waktu berjalan.

                self.pitch_history.append(pitch)
                # Menambahkan pitch ke riwayat.

                self.rms_history.append(rms)
                # Menambahkan RMS ke riwayat.

                self.waveform = audio.copy()
                # Menyimpan waveform terbaru.

                completed = self.segmentor.process(
                    pitch,
                    rms,
                    self._frame_idx
                )
                # Memproses segmentasi suku kata.

                if completed:
                    self.new_syllable = completed
                    # Menyimpan suku kata yang baru selesai.

            self._frame_idx += 1
            # Berpindah ke frame berikutnya.

    def get_state(self) -> dict:
        # Mengambil seluruh status sistem saat ini.

        with self._lock:
            # Mengunci data selama pembacaan.

            return {
                "pitch": self.current_pitch,
                # Pitch saat ini.

                "rms": self.current_rms,
                # RMS saat ini.

                "note": self.current_note,
                # Informasi nada saat ini.

                "score": dict(self.current_score),
                # Informasi skor saat ini.

                "ref_hz": self.current_ref,
                # Frekuensi referensi saat ini.

                "elapsed": self._elapsed,
                # Waktu berjalan karaoke.

                "pitch_hist": list(self.pitch_history),
                # Riwayat pitch.

                "rms_hist": list(self.rms_history),
                # Riwayat RMS.

                "waveform": self.waveform.copy(),
                # Waveform audio terbaru.

                "syllables": self.segmentor.syllables.copy(),
                # Daftar suku kata yang telah terdeteksi.

                "new_syl": self.new_syllable,
                # Suku kata baru yang selesai diproses.

                "cents_hist": self.scorer.get_recent_cents(),
                # Riwayat error pitch.

                "ref_hist": self.scorer.get_recent_ref(),
                # Riwayat frekuensi referensi.

                "frame_idx": self._frame_idx,
                # Nomor frame saat ini.
            }

    def pop_new_syllable(self) -> dict | None:
        # Mengambil suku kata baru lalu menghapusnya dari buffer.

        with self._lock:
            # Mengunci data selama akses.

            s = self.new_syllable
            # Menyimpan suku kata terbaru.

            self.new_syllable = None
            # Mengosongkan buffer suku kata baru.

            return s
            # Mengembalikan suku kata yang diambil.