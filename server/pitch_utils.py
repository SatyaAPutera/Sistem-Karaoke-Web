"""
=================================================================
  PITCH UTILS — Server-side pitch detection & scoring utilities
  
  Algoritma  : YIN (de Cheveigné & Kawahara, JASA 2002)
  Fungsi     : Dipakai oleh main.py untuk real-time karaoke scoring
=================================================================
"""

import numpy as np

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
#  YIN PITCH DETECTION
# ─────────────────────────────────────────────
def yin_pitch(
    signal: np.ndarray,
    sr: int,
    threshold: float = 0.10,
    freq_min: float = 80.0,
    freq_max: float = 1100.0,
) -> float | None:
    """
    Algoritma YIN untuk deteksi pitch real-time.
    
    Parameters
    ----------
    signal   : array audio mono float32/float64
    sr       : sample rate (Hz)
    threshold: YIN threshold (default 0.15, turunkan untuk lebih sensitif)
    freq_min : batas bawah frekuensi yang dideteksi (Hz)
    freq_max : batas atas frekuensi yang dideteksi (Hz)
    
    Returns
    -------
    float pitch dalam Hz, atau None jika tidak ada pitch jelas
    """
    n = len(signal)
    if n < 512:
        return None

    # Ambil maks 4096 sample untuk efisiensi
    n = min(n, 4096)
    x = signal[:n].astype(np.float64)

    # Batas tau (periode sinyal)
    tau_min = max(2, int(sr / freq_max))
    tau_max = min(n // 2 - 1, int(sr / freq_min) + 1)

    if tau_min >= tau_max:
        return None

    # ── Langkah 1: Difference Function ────────────────────────────
    d = np.zeros(tau_max + 1)
    for tau in range(1, tau_max + 1):
        diff = x[: n - tau] - x[tau:n]
        d[tau] = float(np.dot(diff, diff))

    # ── Langkah 2: Cumulative Mean Normalized Difference (CMND) ───
    cmnd = np.ones(tau_max + 1)
    running_sum = 0.0
    for tau in range(1, tau_max + 1):
        running_sum += d[tau]
        if running_sum > 0:
            cmnd[tau] = d[tau] * tau / running_sum
        else:
            cmnd[tau] = 1.0

    # ── Langkah 3: Ambang batas absolut ───────────────────────────
    tau_est = None
    for tau in range(tau_min, tau_max):
        if cmnd[tau] < threshold:
            # Geser ke minimum lokal
            while tau + 1 < tau_max and cmnd[tau + 1] < cmnd[tau]:
                tau += 1
            tau_est = tau
            break

    # Fallback: ambil global minimum jika tidak ada di bawah threshold
    if tau_est is None:
        region = cmnd[tau_min: tau_max + 1]
        local_min_idx = int(np.argmin(region))
        if region[local_min_idx] > 0.45:
            return None          # Tidak ada pitch yang cukup jelas
        tau_est = local_min_idx + tau_min

    # ── Langkah 4: Parabolic Interpolation ────────────────────────
    if 0 < tau_est < tau_max:
        s0 = cmnd[tau_est - 1]
        s1 = cmnd[tau_est]
        s2 = cmnd[tau_est + 1]
        denom = 2.0 * s1 - s0 - s2
        if abs(denom) > 1e-10:
            tau_frac = tau_est + (s2 - s0) / (2.0 * denom)
        else:
            tau_frac = float(tau_est)
    else:
        tau_frac = float(tau_est)

    if tau_frac <= 0:
        return None

    freq = sr / tau_frac
    if freq_min <= freq <= freq_max:
        return freq
    return None


# ─────────────────────────────────────────────
#  SCORING
# ─────────────────────────────────────────────
def score_pitch(user_hz: float, ref_hz: float) -> float:
    """
    Hitung skor (0–100) berdasarkan deviasi pitch dalam cents.
    
    Rubrik:
      ≤ 25 cents  → 100–80  (Perfect / Bagus sekali)
      ≤ 50 cents  → 80–50   (Good / Bagus)
      ≤ 100 cents → 50–0    (OK / Cukup)
      > 100 cents →  0      (Miss)
    """
    if ref_hz <= 0 or user_hz <= 0:
        return 0.0
    cents = abs(1200.0 * np.log2(user_hz / ref_hz))
    if cents <= 25:
        return 100.0 - (cents / 25.0) * 20.0
    if cents <= 50:
        return 80.0 - ((cents - 25.0) / 25.0) * 30.0
    if cents <= 100:
        return 50.0 - ((cents - 50.0) / 50.0) * 50.0
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
    if abs(cents) <= 15:
        return "PERFECT"
    if abs(cents) <= 35:
        return "GOOD"
    if abs(cents) <= 60:
        return "OK"
    return "MISS"
