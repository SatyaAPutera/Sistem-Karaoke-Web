"""
=============================================================
  EVALUASI VOICE SEPARATOR — Karaoke Project
  Pengujian kualitas pemisahan tanpa memerlukan mikrofon.

  Metrics yang diukur:
    - SDR  (Signal-to-Distortion Ratio)  — lebih tinggi = lebih baik
    - SIR  (Signal-to-Interference Ratio)
    - SNR  (Signal-to-Noise Ratio)
    - Pitch accuracy setelah separasi vs langsung dari vokal asli

  Test case:
    1. Ideal   : sinyal sinus bersih (batas atas performa)
    2. Karaoke : vokal + chord musik multi-frekuensi
    3. Noisy   : karaoke + white noise tambahan (simulasi jaringan buruk)
    4. Fallback: tanpa referensi musik (SS saja)
=============================================================
"""

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.signal import stft

from voice_separator import (
    VoiceSeparator, OfflineVoiceSeparator,
    SAMPLE_RATE, N_FFT, sdr
)

# Patch yin_pitch dari modul sebelah jika ada
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pitch_detector'))
try:
    from pitch_detector import yin_pitch, freq_to_note
except ImportError:
    def yin_pitch(buf, sr): return None
    def freq_to_note(f): return None

# ─────────────────────────────────────────────────────────────
#  GENERATOR SINYAL TEST
# ─────────────────────────────────────────────────────────────
SR   = SAMPLE_RATE
DUR  = 3.0
N    = int(SR * DUR)
T    = np.linspace(0, DUR, N)

# Vokal: nada berubah (melodi sederhana C4→E4→G4)
MELODY = [
    (261.6, 0.0, 1.0),    # C4 selama 1 detik
    (329.6, 1.0, 2.0),    # E4 selama 1 detik
    (392.0, 2.0, 3.0),    # G4 selama 1 detik
]

def make_vocal(volume: float = 0.6, noise: float = 0.01) -> np.ndarray:
    """Sinyal vokal: melodi C4→E4→G4 + harmonik + sedikit noise."""
    v = np.zeros(N)
    for freq, t_start, t_end in MELODY:
        mask = (T >= t_start) & (T < t_end)
        # Fundamental + harmonik ke-2 (simulasi timbre vokal)
        v[mask] += (
            0.7 * np.sin(2 * np.pi * freq       * T[mask]) +
            0.2 * np.sin(2 * np.pi * freq * 2   * T[mask]) +
            0.1 * np.sin(2 * np.pi * freq * 3   * T[mask])
        )
    v *= volume
    v += noise * np.random.randn(N)
    return v.astype(np.float32)

def make_music(volume: float = 0.4, noise: float = 0.02) -> np.ndarray:
    """Musik: chord C mayor + bass + perkusi."""
    CHORDS = [
        (261.6, 0.20), (329.6, 0.15), (392.0, 0.12),  # C E G (C4)
        (130.8, 0.18), (164.8, 0.12), (196.0, 0.10),  # C3 E3 G3
        (65.4,  0.08),                                  # C2 bass
    ]
    m = np.zeros(N)
    for freq, amp in CHORDS:
        m += amp * np.sin(2 * np.pi * freq * T)
    # Drum sederhana (impuls setiap beat)
    beat_period = int(SR * 0.5)
    for beat in range(0, N, beat_period):
        env_len = min(600, N - beat)
        env = np.exp(-np.arange(env_len) * 0.006)
        m[beat:beat + env_len] += 0.08 * env * np.random.randn(env_len)
    m *= volume
    m += noise * np.random.randn(N)
    return m.astype(np.float32)

def sir(reference: np.ndarray, estimated: np.ndarray,
        interference: np.ndarray) -> float:
    """Signal-to-Interference Ratio."""
    ref = reference[:len(estimated)]
    est = estimated[:len(ref)]
    intf = interference[:len(ref)]
    sig_pow  = np.mean(ref  ** 2) + 1e-10
    intf_pow = np.mean(intf ** 2) + 1e-10
    return float(10 * np.log10(sig_pow / intf_pow))


# ─────────────────────────────────────────────────────────────
#  TEST CASES
# ─────────────────────────────────────────────────────────────
def run_tests():
    vocal = make_vocal()
    music = make_music()
    mixed = vocal + music

    tests = {}

    print("\n" + "=" * 55)
    print("  EVALUASI VOICE SEPARATOR")
    print("=" * 55)

    # ── Test 1: Wiener dengan referensi bersih
    est_wiener = OfflineVoiceSeparator.separate_wiener(mixed, music, SR)
    sdr_w      = sdr(vocal, est_wiener)
    tests['wiener_clean'] = {
        'label'      : 'Wiener (ref bersih)',
        'estimated'  : est_wiener,
        'sdr'        : sdr_w,
        'color'      : '#00d4aa',
    }
    print(f"  Wiener (ref bersih)    : SDR = {sdr_w:+.1f} dB")

    # ── Test 2: Wiener dengan referensi noisy (simulasi jitter jaringan)
    music_noisy = music + 0.03 * np.random.randn(N).astype(np.float32)
    est_wn      = OfflineVoiceSeparator.separate_wiener(mixed, music_noisy, SR)
    sdr_wn      = sdr(vocal, est_wn)
    tests['wiener_noisy'] = {
        'label'      : 'Wiener (ref+noise)',
        'estimated'  : est_wn,
        'sdr'        : sdr_wn,
        'color'      : '#7c6fff',
    }
    print(f"  Wiener (ref+noise)     : SDR = {sdr_wn:+.1f} dB")

    # ── Test 3: Spectral Subtraction (tanpa referensi)
    est_ss  = OfflineVoiceSeparator.separate_ss(mixed, SR)
    sdr_ss  = sdr(vocal, est_ss)
    tests['ss'] = {
        'label'    : 'Spectral Subtraction',
        'estimated': est_ss,
        'sdr'      : sdr_ss,
        'color'    : '#ffb84d',
    }
    print(f"  Spectral Subtraction   : SDR = {sdr_ss:+.1f} dB")

    # ── Test 4: Input mentah (tanpa separasi) — baseline
    sdr_raw = sdr(vocal, mixed)
    tests['raw'] = {
        'label'    : 'Tanpa separasi (raw)',
        'estimated': mixed,
        'sdr'      : sdr_raw,
        'color'    : '#ff6b6b',
    }
    print(f"  Tanpa separasi (raw)   : SDR = {sdr_raw:+.1f} dB")

    # ── Test 5: Referensi ideal
    sdr_ideal = sdr(vocal, vocal)
    print(f"  Ideal (vokal asli)     : SDR = {sdr_ideal:+.1f} dB  ← batas atas")

    # ── Pitch accuracy per metode
    print(f"\n  {'─'*48}")
    print(f"  Pitch accuracy (pada nada C4=261.6Hz, E4=329.6Hz, G4=392.0Hz):")
    TARGET_NOTES = [('C4', 261.6, 0.0, 1.0),
                    ('E4', 329.6, 1.0, 2.0),
                    ('G4', 392.0, 2.0, 3.0)]

    for note_name, target_hz, t_s, t_e in TARGET_NOTES:
        s_i = int(t_s * SR)
        e_i = int(t_e * SR)
        chunk_size = N_FFT * 2  # ~185ms

        for key in ['wiener_clean', 'wiener_noisy', 'ss', 'raw']:
            est = tests[key]['estimated'][s_i:s_i + chunk_size]
            pitch = yin_pitch(est, SR)
            if pitch:
                err = abs(pitch - target_hz) / target_hz * 100
                print(f"    [{note_name}] {tests[key]['label']:25s} "
                      f"→ {pitch:6.1f}Hz  err={err:.1f}%")
            else:
                print(f"    [{note_name}] {tests[key]['label']:25s} "
                      f"→ tidak terdeteksi")

    return tests, vocal, music, mixed


# ─────────────────────────────────────────────────────────────
#  VISUALISASI HASIL EVALUASI
# ─────────────────────────────────────────────────────────────
def plot_results(tests: dict, vocal, music, mixed):
    BG      = '#0d0d14'
    SURFACE = '#13131e'
    TEXT    = '#e0e0f0'
    MUTED   = '#5a5a78'
    BORDER  = '#2a2a3a'

    plt.rcParams.update({
        'axes.facecolor'   : SURFACE,
        'figure.facecolor' : BG,
        'text.color'       : TEXT,
        'axes.labelcolor'  : MUTED,
        'xtick.color'      : MUTED,
        'ytick.color'      : MUTED,
        'axes.edgecolor'   : BORDER,
        'grid.color'       : BORDER,
        'grid.linewidth'   : 0.5,
        'font.family'      : 'monospace',
    })

    fig = plt.figure(figsize=(15, 9), facecolor=BG)
    fig.canvas.manager.set_window_title("Evaluasi Voice Separator — Karaoke Project")
    gs  = gridspec.GridSpec(3, 4, figure=fig,
                            hspace=0.55, wspace=0.4,
                            left=0.06, right=0.97, top=0.88, bottom=0.07)

    fig.text(0.5, 0.95, "📊  EVALUASI VOICE SEPARATOR",
             ha='center', fontsize=14, color=TEXT,
             fontweight='bold', fontfamily='monospace')
    fig.text(0.5, 0.92,
             "Wiener Filter vs Spectral Subtraction  ·  Metrik: SDR (dB)",
             ha='center', fontsize=8, color=MUTED)

    T_plot = np.linspace(0, DUR, N)

    # Row 0: sinyal input
    ax = fig.add_subplot(gs[0, :2])
    ax.plot(T_plot, vocal, lw=0.8, color='#00d4aa', alpha=0.9, label='Vokal')
    ax.plot(T_plot, music, lw=0.8, color='#ff6b6b', alpha=0.7, label='Musik')
    ax.set_title("SINYAL INPUT", fontsize=8, color=MUTED, loc='left', pad=3)
    ax.set_ylabel("Amplitudo", fontsize=7)
    ax.set_xlabel("Waktu (s)", fontsize=7)
    ax.legend(fontsize=7, facecolor='#1c1c2a', edgecolor=BORDER, labelcolor=TEXT)
    ax.grid(True, alpha=0.2)

    ax = fig.add_subplot(gs[0, 2:])
    ax.plot(T_plot, mixed, lw=0.8, color='#7c6fff', alpha=0.9)
    ax.set_title("CAMPURAN (Input Mic)", fontsize=8, color=MUTED, loc='left', pad=3)
    ax.set_ylabel("Amplitudo", fontsize=7)
    ax.set_xlabel("Waktu (s)", fontsize=7)
    ax.grid(True, alpha=0.2)

    # Row 1: hasil tiap metode
    for i, (key, info) in enumerate(tests.items()):
        ax = fig.add_subplot(gs[1, i])
        ax.plot(T_plot, vocal[:N],             lw=0.8, color='#00d4aa', alpha=0.5,
                label='vokal asli')
        est = info['estimated']
        ax.plot(T_plot[:len(est)], est,        lw=1.0, color=info['color'], alpha=0.9,
                label='estimasi')
        ax.set_title(f"{info['label']}\nSDR = {info['sdr']:+.1f} dB",
                     fontsize=8, color=TEXT, loc='left', pad=3)
        ax.set_xlabel("Waktu (s)", fontsize=7)
        ax.legend(fontsize=6, facecolor='#1c1c2a', edgecolor=BORDER, labelcolor=TEXT)
        ax.grid(True, alpha=0.2)

    # Row 2 kiri: SDR bar chart
    ax = fig.add_subplot(gs[2, :2])
    labels = [v['label'] for v in tests.values()]
    sdrs   = [v['sdr']   for v in tests.values()]
    colors = [v['color'] for v in tests.values()]
    bars = ax.barh(labels, sdrs, color=colors, alpha=0.85, height=0.55)
    ax.axvline(0, color=BORDER, lw=0.8)
    ax.set_title("PERBANDINGAN SDR", fontsize=8, color=MUTED, loc='left', pad=3)
    ax.set_xlabel("SDR (dB) — lebih tinggi lebih baik", fontsize=7)
    ax.grid(True, axis='x', alpha=0.2)
    for bar, val in zip(bars, sdrs):
        ax.text(val + 0.3, bar.get_y() + bar.get_height()/2.,
                f"{val:+.1f} dB", va='center', fontsize=8,
                color=TEXT, fontfamily='monospace')

    # Row 2 kanan: Spectrogram perbandingan (mixed vs wiener)
    ax = fig.add_subplot(gs[2, 2:])
    CHUNK = 512
    # Ambil 1 detik pertama
    seg_mixed  = mixed[:SR].astype(np.float64)
    seg_wiener = tests['wiener_clean']['estimated'][:SR].astype(np.float64)

    f, t, Zm = stft(seg_mixed,  SR, nperseg=512, noverlap=384)
    _, _, Zw = stft(seg_wiener, SR, nperseg=512, noverlap=384)

    # Tampilkan magnitude dalam dB (0–4000 Hz)
    freq_max_idx = np.searchsorted(f, 4000)
    Zm_db = 20 * np.log10(np.abs(Zm[:freq_max_idx]) + 1e-6)
    Zw_db = 20 * np.log10(np.abs(Zw[:freq_max_idx]) + 1e-6)

    # Side by side dalam satu plot
    combined = np.hstack([Zm_db, np.ones((Zm_db.shape[0], 3)) * -60, Zw_db])
    im = ax.imshow(combined, aspect='auto', origin='lower',
                   cmap='magma', vmin=-80, vmax=0,
                   extent=[0, combined.shape[1], 0, 4000])
    ax.set_title("SPEKTROGRAM: Campuran (kiri) | Wiener (kanan)",
                 fontsize=8, color=MUTED, loc='left', pad=3)
    ax.set_ylabel("Frekuensi (Hz)", fontsize=7)
    ax.set_xlabel("Frame", fontsize=7)
    ax.axvline(Zm_db.shape[1] + 1.5, color=TEXT, lw=1, ls='--', alpha=0.5)
    plt.colorbar(im, ax=ax, label='dB', pad=0.01)

    plt.tight_layout(rect=[0, 0, 1, 0.90])
    plt.show()


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests, vocal, music, mixed = run_tests()
    plot_results(tests, vocal, music, mixed)
