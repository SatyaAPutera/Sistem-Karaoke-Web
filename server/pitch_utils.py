"""
=================================================================
  PITCH UTILS — Server-side pitch detection & scoring utilities

  Algoritma  : YIN (de Cheveigné & Kawahara, JASA 2002)
               + FFT-based difference function (performa)
               + PitchTracker: median filter + koreksi oktaf +
                 EMA smoothing + voiced-hold, agar hasil pitch
                 tracking lebih halus & tidak "patah-patah" ala Smule
  Fungsi     : Dipakai oleh main.py untuk real-time karaoke scoring

  CATATAN INTEGRASI:
  - yin_pitch() SIGNATURE & RETURN VALUE TIDAK BERUBAH (tetap
    float | None), jadi kode lama di main.py yang memanggil
    yin_pitch() langsung tetap jalan tanpa modifikasi.
  - Untuk hasil yang halus, gunakan PitchTracker (baru) — buat SATU
    instance per koneksi/sesi karaoke (bukan global!), lalu panggil
    tracker.process(chunk) tiap kali ada frame audio baru dari
    Satya punya socket layer. Contoh pemakaian ada di bagian bawah.
=================================================================
"""

import numpy as np
from collections import deque

# ─────────────────────────────────────────────
#  NOTE MAPPING
# ─────────────────────────────────────────────
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F',
              'F#', 'G', 'G#', 'A', 'A#', 'B']


def freq_to_note(freq: float) -> dict | None:
    """Konversi frekuensi (Hz) → informasi nada musik."""
    if freq is None or freq <= 0:
        return None
    semitones = 12.0 * np.log2(freq / 440.0) + 69.0
    midi_note = int(round(semitones))
    name = NOTE_NAMES[midi_note % 12]
    octave = (midi_note // 12) - 1
    cents = round((semitones - midi_note) * 100)
    return {
        "name": f"{name}{octave}",
        "note": name,
        "octave": octave,
        "cents": cents,
        "midi": midi_note,
    }


def midi_to_hz(midi_note: int) -> float:
    """Nomor MIDI → frekuensi Hz."""
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


# ─────────────────────────────────────────────
#  YIN — DIFFERENCE FUNCTION (versi FFT, jauh lebih cepat)
# ─────────────────────────────────────────────
def _difference_function_fft(x: np.ndarray, tau_max: int) -> np.ndarray:
    """
    Hitung fungsi selisih YIN d(tau) memakai autokorelasi berbasis FFT.

    d(tau) = sum_j (x[j] - x[j+tau])^2
           = E1(tau) + E2(tau) - 2 * acf(tau)

    Loop asli d(tau) = sum((x[:n-tau] - x[tau:])**2) untuk semua tau
    berkompleksitas O(n * tau_max) — ini bottleneck utama yang memaksa
    kita pakai hop size besar (update pitch jarang => hasil di client
    terasa 'patah-patah'/step-y). Versi FFT ini O(n log n), sehingga
    frame bisa diproses lebih sering (hop lebih kecil) dengan beban
    CPU yang jauh lebih murah.
    """
    n = len(x)
    size = 1
    while size < 2 * n:
        size *= 2

    X = np.fft.rfft(x, size)
    acf_full = np.fft.irfft(X * np.conj(X))[:n]

    x_sq = x * x
    cumsum = np.concatenate(([0.0], np.cumsum(x_sq)))
    total_energy = cumsum[n]

    tau_max = min(tau_max, n - 1)
    taus = np.arange(1, tau_max + 1)

    e1 = cumsum[n - taus]              # energi x[0 : n-tau]
    e2 = total_energy - cumsum[taus]   # energi x[tau : n]
    acf = acf_full[taus]

    d = np.zeros(tau_max + 1)
    d[1:] = e1 + e2 - 2.0 * acf
    return d


# ─────────────────────────────────────────────
#  YIN CORE — mengembalikan (freq, confidence) untuk 1 frame
# ─────────────────────────────────────────────
def _yin_core(
    signal: np.ndarray,
    sr: int,
    threshold: float,
    freq_min: float,
    freq_max: float,
) -> tuple[float, float] | None:
    n = len(signal)
    if n < 512:
        return None

    n = min(n, 4096)
    x = signal[:n].astype(np.float64)
    x = x - np.mean(x)          # buang DC offset -> kurangi lompatan oktaf palsu

    tau_min = max(2, int(sr / freq_max))
    tau_max = min(n // 2 - 1, int(sr / freq_min) + 1)
    if tau_min >= tau_max:
        return None

    d = _difference_function_fft(x, tau_max)

    # ── Cumulative Mean Normalized Difference (CMND) ──────────────
    cmnd = np.ones(tau_max + 1)
    running_sum = 0.0
    for tau in range(1, tau_max + 1):
        running_sum += d[tau]
        cmnd[tau] = d[tau] * tau / running_sum if running_sum > 0 else 1.0

    # ── Ambang batas absolut ───────────────────────────────────────
    tau_est = None
    for tau in range(tau_min, tau_max):
        if cmnd[tau] < threshold:
            while tau + 1 < tau_max and cmnd[tau + 1] < cmnd[tau]:
                tau += 1
            tau_est = tau
            break

    if tau_est is None:
        region = cmnd[tau_min: tau_max + 1]
        local_min_idx = int(np.argmin(region))
        if region[local_min_idx] > 0.45:
            return None          # Tidak ada pitch yang cukup jelas
        tau_est = local_min_idx + tau_min

    # ── Parabolic Interpolation ────────────────────────────────────
    if 0 < tau_est < tau_max:
        s0, s1, s2 = cmnd[tau_est - 1], cmnd[tau_est], cmnd[tau_est + 1]
        denom = 2.0 * s1 - s0 - s2
        tau_frac = tau_est + (s2 - s0) / (2.0 * denom) if abs(denom) > 1e-10 else float(tau_est)
    else:
        tau_frac = float(tau_est)

    if tau_frac <= 0:
        return None

    freq = sr / tau_frac
    if not (freq_min <= freq <= freq_max):
        return None

    confidence = float(np.clip(1.0 - cmnd[tau_est], 0.0, 1.0))
    return freq, confidence


# ─────────────────────────────────────────────
#  YIN PITCH DETECTION (interface lama, tetap kompatibel)
# ─────────────────────────────────────────────
def yin_pitch(
    signal: np.ndarray,
    sr: int,
    threshold: float = 0.15,
    freq_min: float = 80.0,
    freq_max: float = 1100.0,
) -> float | None:
    """
    Algoritma YIN untuk deteksi pitch pada satu frame (single-shot,
    tanpa memori antar-frame). SIGNATURE & RETURN VALUE sama persis
    seperti versi sebelumnya, jadi kode lama tetap kompatibel.

    Untuk hasil real-time yang halus (tidak patah-patah), gunakan
    PitchTracker di bawah alih-alih memanggil fungsi ini langsung
    tiap frame.
    """
    result = _yin_core(signal, sr, threshold, freq_min, freq_max)
    return result[0] if result is not None else None


# ─────────────────────────────────────────────
#  PITCH TRACKER — smoothing antar-frame (biar tidak patah-patah)
# ─────────────────────────────────────────────
class PitchTracker:
    """
    Wrapper stateful di atas YIN untuk SATU sesi/koneksi karaoke.

    Menambahkan 4 lapisan smoothing yang membuat pitch curve terasa
    mulus seperti di Smule, alih-alih melompat tiap frame:

      1. Median filter beberapa frame terakhir
         -> buang outlier 1-frame (mis. karena noise mic).
      2. Koreksi lompatan oktaf
         -> jika hasil frame baru ≈2x / 0.5x dari pitch stabil
            sebelumnya, dianggap error oktaf dan diluruskan.
      3. Exponential Moving Average (EMA)
         -> transisi antar nada jadi landai, bukan patah tegas.
      4. Voiced-hold
         -> saat suara sempat putus sangat singkat (ambil napas,
            noise mic, dsb), pitch terakhir yang valid tetap
            'ditahan' beberapa frame dulu, alih-alih langsung
            jatuh ke None. Baru dianggap silence sungguhan setelah
            N frame berturut-turut tanpa pitch terdeteksi.

    PENTING: buat SATU instance PitchTracker per user/koneksi
    (misalnya disimpan di dict `trackers[session_id] = PitchTracker(...)`
    di sisi socket server Satya), JANGAN dipakai bareng-bareng lintas
    user karena state-nya per sesi.

    Contoh pemakaian:

        trackers = {}  # session_id -> PitchTracker

        def on_audio_chunk(session_id, chunk, sr):
            if session_id not in trackers:
                trackers[session_id] = PitchTracker(sr=sr)
            result = trackers[session_id].process(chunk)
            if result["voiced"]:
                score = score_pitch(result["freq"], ref_hz)
                # kirim result["freq"], result["note"], score ke client
            else:
                # user diam / jeda -> tidak usah scoring frame ini
                ...

        def on_session_end(session_id):
            trackers.pop(session_id, None)
    """

    def __init__(
        self,
        sr: int,
        median_window: int = 5,
        ema_alpha: float = 0.35,
        hold_frames: int = 6,
        octave_tolerance_cents: float = 50.0,
        yin_threshold: float = 0.15,
        freq_min: float = 80.0,
        freq_max: float = 1100.0,
    ):
        """
        Parameters
        ----------
        sr                      : sample rate (Hz)
        median_window           : jumlah frame utk median filter (ganjil, 3-7 disarankan)
        ema_alpha               : bobot EMA (0-1). Makin kecil -> makin halus tapi
                                   makin 'lambat' mengikuti perubahan nada asli.
                                   0.3-0.4 cocok utk karaoke real-time.
        hold_frames             : berapa frame berturut-turut suara 'hilang' yang
                                   masih dianggap jeda sesaat (bukan silence),
                                   selama itu pitch terakhir tetap ditahan.
        octave_tolerance_cents  : toleransi (dalam cents) utk deteksi lompatan oktaf.
        """
        self.sr = sr
        self.yin_threshold = yin_threshold
        self.freq_min = freq_min
        self.freq_max = freq_max

        self.median_window = median_window
        self.ema_alpha = ema_alpha
        self.hold_frames = hold_frames
        self.octave_tolerance_cents = octave_tolerance_cents

        self._raw_history: deque = deque(maxlen=median_window)
        self._smoothed_freq: float | None = None
        self._unvoiced_count: int = 0
        self._last_valid_freq: float | None = None

    def reset(self):
        """Panggil saat mulai lagu baru / user berhenti bernyanyi."""
        self._raw_history.clear()
        self._smoothed_freq = None
        self._unvoiced_count = 0
        self._last_valid_freq = None

    def _correct_octave_jump(self, freq: float) -> float:
        """Luruskan freq jika beda oktaf tipis (0.5x/2x/3x/1/3x) dari pitch stabil terakhir."""
        if self._smoothed_freq is None:
            return freq
        for ratio in (2.0, 0.5, 3.0, 1 / 3.0):
            candidate = freq * ratio
            cents_diff = abs(1200.0 * np.log2(candidate / self._smoothed_freq))
            if cents_diff < self.octave_tolerance_cents:
                return candidate
        return freq

    def process(self, signal: np.ndarray) -> dict:
        """
        Proses satu frame audio (mono, float). Panggil tiap kali ada
        chunk baru dari client — idealnya hop kecil (~10-20ms) untuk
        hasil paling halus.

        Returns
        -------
        dict dengan keys:
          - freq       : float | None  (pitch setelah smoothing penuh)
          - confidence : float 0..1    (0 saat frame sedang di-hold)
          - note       : dict | None   (hasil freq_to_note)
          - voiced     : bool          (False hanya kalau benar-benar silence)
        """
        raw = _yin_core(signal, self.sr, self.yin_threshold, self.freq_min, self.freq_max)

        if raw is None:
            self._unvoiced_count += 1
            if self._last_valid_freq is not None and self._unvoiced_count <= self.hold_frames:
                # Gap pendek (napas/noise sesaat) -> tahan pitch terakhir
                freq = self._last_valid_freq
                confidence = 0.0
            else:
                # Gap sudah cukup panjang -> silence sungguhan, reset state
                self._raw_history.clear()
                self._smoothed_freq = None
                self._last_valid_freq = None
                return {"freq": None, "confidence": 0.0, "note": None, "voiced": False}
        else:
            freq, confidence = raw
            freq = self._correct_octave_jump(freq)
            self._unvoiced_count = 0
            self._last_valid_freq = freq

            # 1) Median filter -> buang outlier 1-frame
            self._raw_history.append(freq)
            freq = float(np.median(self._raw_history))

        # 2) EMA smoothing -> transisi antar nada jadi landai
        if self._smoothed_freq is None:
            self._smoothed_freq = freq
        else:
            a = self.ema_alpha
            self._smoothed_freq = a * freq + (1 - a) * self._smoothed_freq

        note_info = freq_to_note(self._smoothed_freq)
        return {
            "freq": self._smoothed_freq,
            "confidence": confidence,
            "note": note_info,
            "voiced": True,
        }


# ─────────────────────────────────────────────
#  SCORING  (logika tidak diubah — tetap kompatibel dengan main.py)
# ─────────────────────────────────────────────
def score_pitch(user_hz: float, ref_hz: float) -> float:
    """
    Hitung skor (0-100) berdasarkan deviasi pitch dalam cents dengan Octave Matching.
    Mendukung pergeseran oktaf otomatis agar penyanyi dengan tipe vokal berbeda (bass/tenor/sopran)
    tetap mendapatkan nilai tinggi jika nadanya tepat.

    TIPS INTEGRASI: idealnya `user_hz` di sini adalah hasil
    PitchTracker.process(...)["freq"] (sudah dihaluskan), bukan hasil
    langsung yin_pitch() per frame, supaya skor tidak ikut 'gemetar'
    karena noise 1-frame.

    Rubrik Longgar Baru:
      ≤ 35 cents  → 100-85  (Perfect / Sangat Bagus)
      ≤ 75 cents  → 85-60   (Good / Bagus)
      ≤ 150 cents → 60-10   (OK / Cukup)
      > 150 cents → 0       (Miss)
    """
    if ref_hz <= 0 or user_hz <= 0:
        return 0.0

    # ── Octave Matching ──────────────────────────────────────────
    octave_diff = round(np.log2(user_hz / ref_hz))
    user_hz_aligned = user_hz / (2.0 ** octave_diff)

    cents = abs(1200.0 * np.log2(user_hz_aligned / ref_hz))

    if cents <= 35:
        return 100.0 - (cents / 35.0) * 15.0
    if cents <= 75:
        return 85.0 - ((cents - 35.0) / 40.0) * 25.0
    if cents <= 150:
        return 60.0 - ((cents - 75.0) / 75.0) * 50.0
    return 0.0


def get_grade(score: float) -> tuple[str, str]:
    """
    Konversi skor numerik → grade huruf + label.
    Returns (grade, label).
    """
    if score >= 95:
        return ("S", "SEMPURNA!")
    if score >= 85:
        return ("A", "LUAR BIASA!")
    if score >= 75:
        return ("B", "BAGUS!")
    if score >= 60:
        return ("C", "CUKUP BAIK")
    if score >= 40:
        return ("D", "PERLU LATIHAN")
    return ("F", "COBA LAGI")


def get_pitch_label(cents: float) -> str:
    """Label akurasi nada berdasarkan deviasi cents."""
    if cents is None:
        return ""
    if abs(cents) <= 35:
        return "PERFECT"
    if abs(cents) <= 75:
        return "GOOD"
    if abs(cents) <= 150:
        return "OK"
    return "MISS"