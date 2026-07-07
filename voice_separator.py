"""
=============================================================
  VOICE SEPARATOR — Karaoke Project
  Modul: Pemisahan Suara Vokal dari Musik (Real-time)

  Konteks proyek:
    - Server mengirim audio musik ke client
    - Client merekam via mic: hasil gabungan vokal + musik
    - Modul ini memisahkan vokal dari campuran tersebut
    - Output vokal → Pitch Detector (server/pitch_utils.py)

  Metode:
    1. PRIMARY  — Wiener Filter berbasis referensi musik
       Digunakan saat audio referensi musik dari server tersedia.
       SDR ~30 dB pada kondisi ideal.

    2. FALLBACK — Spectral Subtraction adaptif
       Digunakan saat referensi tidak tersedia / terlambat.
       SDR ~15–20 dB, cukup untuk pitch detection.

  Referensi:
    Boll (1979) - Spectral subtraction
    Scalart & Filho (1996) - Wiener filter untuk speech enhancement
=============================================================
"""

import numpy as np
from scipy.signal import stft, istft, get_window
import threading
import queue
import collections


# ─────────────────────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────────────────────
SAMPLE_RATE     = 44100     # Hz
N_FFT           = 2048      # Ukuran FFT window
HOP_LENGTH      = 512       # Overlap antar frame (75% overlap)
WINDOW_TYPE     = 'hann'    # Jenis window

# Wiener filter
WIENER_BETA     = 0.001     # Floor spektral (cegah silence total)
WIENER_ALPHA    = 1.0       # Over-subtraction factor

# Spectral subtraction (fallback)
SS_ALPHA        = 2.0       # Over-subtraction (lebih agresif)
SS_BETA         = 0.005     # Spectral floor
SS_NOISE_FRAMES = 10        # Frame awal untuk estimasi noise

# Temporal smoothing
SMOOTHING_GAMMA = 0.85      # EMA untuk mask (0 = no smooth, 1 = heavy)

# Gain limiter output
MAX_GAIN        = 2.5       # Batas maksimum gain amplifikasi

# Buffer
BUFFER_SECONDS  = 0.5       # Delay buffer untuk sinkronisasi musik
CHUNK_SIZE      = 1024      # Samples per real-time chunk


# ─────────────────────────────────────────────────────────────
#  UTILITAS
# ─────────────────────────────────────────────────────────────
def rms(x: np.ndarray) -> float:
    """Root Mean Square."""
    return float(np.sqrt(np.mean(x ** 2) + 1e-10))


def normalize(x: np.ndarray, target_rms: float = 0.1) -> tuple[np.ndarray, float]:
    """Normalisasi sinyal ke target RMS. Return (normalized, gain_applied)."""
    current = rms(x)
    if current < 1e-8:
        return x, 1.0
    gain = target_rms / current
    gain = min(gain, MAX_GAIN)
    return x * gain, gain


def sdr(reference: np.ndarray, estimated: np.ndarray) -> float:
    """
    Signal-to-Distortion Ratio dalam dB.
    Metrik kualitas pemisahan — semakin tinggi semakin baik.
    """
    ref = reference[:len(estimated)]
    est = estimated[:len(ref)]
    num = np.mean(ref ** 2) + 1e-10
    den = np.mean((ref - est) ** 2) + 1e-10
    return float(10 * np.log10(num / den))


# ─────────────────────────────────────────────────────────────
#  FRAME PROCESSOR — inti DSP per chunk
# ─────────────────────────────────────────────────────────────
class FrameProcessor:
    """
    Memproses sepasang audio (mixed, reference) per chunk kecil.
    Menjaga state antar-chunk: overlap-add, mask smoothing, dsb.

    Desain: stateful, satu instance per stream.
    """

    def __init__(self):
        self._prev_mask : np.ndarray | None = None
        self._noise_psd : np.ndarray | None = None   # untuk fallback SS
        self._noise_frames_collected : int  = 0
        self._overlap_buf : np.ndarray      = np.zeros(N_FFT)
        self._window   = get_window(WINDOW_TYPE, N_FFT)
        self._n_bins   = N_FFT // 2 + 1

    # ── Internal helpers ─────────────────────────────────────

    def _smooth_mask(self, mask: np.ndarray) -> np.ndarray:
        """
        Temporal EMA smoothing pada mask spektral.
        Mengurangi musical noise (artifact berbunyi 'bergelombang').
        """
        if self._prev_mask is None:
            self._prev_mask = mask.copy()
            return mask
        smoothed = SMOOTHING_GAMMA * self._prev_mask + (1 - SMOOTHING_GAMMA) * mask
        self._prev_mask = smoothed.copy()
        return smoothed

    def _wiener_mask(self,
                     mixed_spec  : np.ndarray,
                     ref_spec    : np.ndarray) -> np.ndarray:
        """
        Hitung Wiener filter mask:
            W(f) = |V(f)|² / (|V(f)|² + α·|M(f)|²)

        dimana:
            |V(f)|² = PSD vokal (estimasi = campuran - musik)
            |M(f)|² = PSD musik (dari referensi server)

        Referensi: Scalart & Filho, ICASSP 1996
        """
        mixed_psd = np.abs(mixed_spec) ** 2
        ref_psd   = np.abs(ref_spec)   ** 2

        # Estimasi PSD vokal: campuran dikurangi referensi
        vocal_psd_est = np.maximum(
            mixed_psd - WIENER_ALPHA * ref_psd,
            WIENER_BETA * mixed_psd
        )

        # Wiener ratio
        mask = vocal_psd_est / (vocal_psd_est + ref_psd + 1e-10)
        mask = np.clip(mask, 0.0, 1.0)
        return mask

    def _spectral_subtraction_mask(self,
                                   mixed_spec : np.ndarray,
                                   is_first   : bool) -> np.ndarray:
        """
        Spectral subtraction adaptif — fallback tanpa referensi musik.

        Estimasi noise PSD dari beberapa frame awal (asumsi awal = musik
        sebelum penyanyi masuk), lalu kurangi dari campuran.

        Referensi: Boll, IEEE Trans. ASSP, 1979
        """
        mixed_psd = np.abs(mixed_spec) ** 2

        # Estimasi noise dari frame awal
        if self._noise_frames_collected < SS_NOISE_FRAMES:
            if self._noise_psd is None:
                self._noise_psd = mixed_psd.copy()
            else:
                # Running average
                n = self._noise_frames_collected
                self._noise_psd = (self._noise_psd * n + mixed_psd) / (n + 1)
            self._noise_frames_collected += 1
            # Belum cukup data → kembalikan sinyal asli
            return np.ones_like(mixed_psd)

        # Over-subtraction
        vocal_psd_est = np.maximum(
            mixed_psd - SS_ALPHA * self._noise_psd,
            SS_BETA * mixed_psd
        )

        mask = np.sqrt(vocal_psd_est / (mixed_psd + 1e-10))
        mask = np.clip(mask, 0.0, 1.0)
        return mask

    # ── Public API ────────────────────────────────────────────

    def process_chunk(self,
                      mixed_chunk : np.ndarray,
                      ref_chunk   : np.ndarray | None = None,
                      mode        : str = 'wiener') -> np.ndarray:
        """
        Proses satu chunk audio.

        Args:
            mixed_chunk : Audio gabungan vokal+musik dari mikrofon client.
                          Shape: (N,), float32
            ref_chunk   : Referensi musik dari server (harus sama panjang).
                          None → gunakan mode fallback SS.
            mode        : 'wiener' | 'ss' (spectral subtraction)

        Returns:
            vocal_chunk : Estimasi sinyal vokal. Shape: (N,), float32
        """
        n = len(mixed_chunk)

        # Pad ke N_FFT jika chunk lebih pendek
        if n < N_FFT:
            mixed_pad = np.zeros(N_FFT)
            mixed_pad[:n] = mixed_chunk
            mixed_chunk = mixed_pad
            if ref_chunk is not None:
                ref_pad = np.zeros(N_FFT)
                ref_pad[:n] = ref_chunk
                ref_chunk = ref_pad

        # ─ STFT
        mixed_spec = np.fft.rfft(mixed_chunk * self._window)  # (N_FFT//2+1,) complex

        # ─ Pilih metode
        if ref_chunk is not None and mode == 'wiener':
            ref_spec = np.fft.rfft(ref_chunk * self._window)
            mask = self._wiener_mask(mixed_spec, ref_spec)
        else:
            mask = self._spectral_subtraction_mask(mixed_spec, False)

        # ─ Temporal smoothing (kurangi musical noise)
        mask = self._smooth_mask(mask)

        # ─ Terapkan mask
        vocal_spec = mask * mixed_spec

        # ─ ISTFT (inverse FFT)
        vocal_frame = np.fft.irfft(vocal_spec)

        # ─ Overlap-add
        out = np.zeros(n)
        overlap_len = min(len(self._overlap_buf), N_FFT)
        frame_len   = min(N_FFT, n + overlap_len)
        vocal_frame = vocal_frame[:frame_len] * self._window[:frame_len]

        # Tambahkan overlap dari frame sebelumnya
        result_len = min(n, N_FFT)
        result     = np.zeros(result_len)
        result    += vocal_frame[:result_len]
        ovl_len    = min(len(self._overlap_buf), result_len)
        result[:ovl_len] += self._overlap_buf[:ovl_len]

        # Simpan sisa untuk frame berikutnya
        if N_FFT > n:
            self._overlap_buf = vocal_frame[n:]
        else:
            self._overlap_buf = np.zeros(N_FFT)

        return result.astype(np.float32)


# ─────────────────────────────────────────────────────────────
#  MUSIC REFERENCE BUFFER — sinkronisasi server audio
# ─────────────────────────────────────────────────────────────
class MusicReferenceBuffer:
    """
    Buffer FIFO untuk audio musik dari server.

    Konteks jaringan:
      - Server stream audio musik → client
      - Ada latensi jaringan: musik bisa tiba lebih awal/lambat
      - Buffer ini menyelaraskan musik referensi dengan audio mic

    Di aplikasi nyata: isi buffer ini dari socket/WebSocket server.
    """

    def __init__(self, max_seconds: float = BUFFER_SECONDS):
        self._buf      : collections.deque = collections.deque()
        self._lock     = threading.Lock()
        self._max_samp = int(max_seconds * SAMPLE_RATE)
        self._total_in = 0    # Total samples yang masuk
        self._total_out= 0    # Total samples yang keluar

    def push(self, audio: np.ndarray):
        """Masukkan chunk musik dari server."""
        with self._lock:
            self._buf.extend(audio.tolist())
            self._total_in += len(audio)
            # Buang jika buffer terlalu besar (lag kompensasi)
            while len(self._buf) > self._max_samp:
                self._buf.popleft()

    def pull(self, n: int) -> np.ndarray | None:
        """
        Ambil n samples dari buffer.
        Returns None jika buffer belum cukup (underrun).
        """
        with self._lock:
            if len(self._buf) < n:
                return None   # Underrun — gunakan fallback SS
            samples = [self._buf.popleft() for _ in range(n)]
            self._total_out += n
            return np.array(samples, dtype=np.float32)

    @property
    def buffered_ms(self) -> float:
        """Berapa milidetik audio yang ada di buffer."""
        with self._lock:
            return len(self._buf) / SAMPLE_RATE * 1000

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "buffered_samples" : len(self._buf),
                "buffered_ms"      : len(self._buf) / SAMPLE_RATE * 1000,
                "total_in"         : self._total_in,
                "total_out"        : self._total_out,
                "drift_samples"    : self._total_in - self._total_out,
            }


# ─────────────────────────────────────────────────────────────
#  VOICE SEPARATOR — interface utama
# ─────────────────────────────────────────────────────────────
class VoiceSeparator:
    """
    Interface utama untuk pemisahan vokal dari campuran vokal+musik.

    Penggunaan di aplikasi karaoke:
    ┌──────────────────────────────────────────────────────┐
    │  server ──(stream musik)──► MusicReferenceBuffer     │
    │                                    │                 │
    │  mic   ──(mixed audio)──► VoiceSeparator.process()  │
    │                                    │                 │
    │                          vocal_only ──► PitchDetector│
    └──────────────────────────────────────────────────────┘
    """

    def __init__(self):
        self.music_buffer  = MusicReferenceBuffer()
        self._processor    = FrameProcessor()
        self._stats        = {
            "chunks_wiener"  : 0,
            "chunks_fallback": 0,
            "total_chunks"   : 0,
        }

    # ── Dari server ──────────────────────────────────────────

    def receive_music(self, audio_chunk: np.ndarray):
        """
        Terima chunk audio musik dari server.
        Panggil setiap kali menerima packet musik dari network.

        Args:
            audio_chunk: Audio musik float32, mono, shape (N,)
        """
        self.music_buffer.push(audio_chunk)

    # ── Proses mic ───────────────────────────────────────────

    def process(self,
                mixed_audio : np.ndarray,
                force_mode  : str | None = None) -> dict:
        """
        Pisahkan vokal dari audio gabungan vokal+musik.

        Args:
            mixed_audio : Audio campuran dari mic client.
                          float32, mono, shape (N,)
            force_mode  : 'wiener' | 'ss' | None (auto)

        Returns:
            dict berisi:
                'vocal'      : np.ndarray — estimasi vokal murni
                'mode'       : str  — metode yang dipakai
                'has_ref'    : bool — apakah referensi musik tersedia
                'buffer_ms'  : float — buffer lag dalam ms
        """
        n      = len(mixed_audio)
        ref    = self.music_buffer.pull(n)
        has_ref = ref is not None

        # Tentukan mode
        if force_mode:
            mode = force_mode
        else:
            mode = 'wiener' if has_ref else 'ss'

        # Proses separasi
        vocal = self._processor.process_chunk(
            mixed_chunk = mixed_audio,
            ref_chunk   = ref if has_ref else None,
            mode        = mode,
        )

        # Update stats
        self._stats['total_chunks'] += 1
        if mode == 'wiener' and has_ref:
            self._stats['chunks_wiener']   += 1
        else:
            self._stats['chunks_fallback'] += 1

        return {
            'vocal'     : vocal,
            'mode'      : mode,
            'has_ref'   : has_ref,
            'buffer_ms' : self.music_buffer.buffered_ms,
        }

    @property
    def stats(self) -> dict:
        """Statistik penggunaan metode dan buffer."""
        total = max(self._stats['total_chunks'], 1)
        return {
            **self._stats,
            "wiener_pct"  : self._stats['chunks_wiener']   / total * 100,
            "fallback_pct": self._stats['chunks_fallback'] / total * 100,
            "buffer"      : self.music_buffer.stats,
        }

    def reset(self):
        """Reset state internal (misal saat lagu ganti)."""
        self._processor = FrameProcessor()
        self.music_buffer = MusicReferenceBuffer()
        self._stats = {
            "chunks_wiener"  : 0,
            "chunks_fallback": 0,
            "total_chunks"   : 0,
        }


# ─────────────────────────────────────────────────────────────
#  OFFLINE PROCESSOR — untuk testing dengan file audio
# ─────────────────────────────────────────────────────────────
class OfflineVoiceSeparator:
    """
    Proses seluruh audio sekaligus (non-realtime).
    Berguna untuk evaluasi, debugging, dan testing kualitas.
    Menggunakan STFT penuh — lebih akurat dari chunk-by-chunk.
    """

    @staticmethod
    def separate_wiener(mixed    : np.ndarray,
                        music_ref: np.ndarray,
                        sr       : int = SAMPLE_RATE,
                        alpha    : float = WIENER_ALPHA,
                        beta     : float = WIENER_BETA) -> np.ndarray:
        """
        Pisahkan vokal dari campuran menggunakan Wiener filter.
        Untuk audio panjang. Gunakan ini untuk evaluasi.

        Args:
            mixed     : Audio campuran vokal+musik
            music_ref : Audio musik referensi (dari server)
            sr        : Sample rate
            alpha     : Over-subtraction factor (1.0 = tanpa penambahan)
            beta      : Spectral floor (cegah silence artifak)

        Returns:
            vocal_estimated : np.ndarray float32
        """
        # Samakan panjang
        min_len   = min(len(mixed), len(music_ref))
        mixed     = mixed[:min_len].astype(np.float64)
        music_ref = music_ref[:min_len].astype(np.float64)

        # STFT
        f, t, M = stft(mixed,     sr, nperseg=N_FFT, noverlap=N_FFT - HOP_LENGTH,
                       window=WINDOW_TYPE)
        _, _, R = stft(music_ref, sr, nperseg=N_FFT, noverlap=N_FFT - HOP_LENGTH,
                       window=WINDOW_TYPE)

        # PSD
        M_psd = np.abs(M) ** 2
        R_psd = np.abs(R) ** 2

        # Estimasi PSD vokal
        V_psd = np.maximum(M_psd - alpha * R_psd, beta * M_psd)

        # Wiener mask — 2D: (freq_bins, time_frames)
        W = V_psd / (V_psd + R_psd + 1e-10)
        W = np.clip(W, 0.0, 1.0)

        # Temporal smoothing (1D over time axis)
        from scipy.ndimage import uniform_filter1d
        W = uniform_filter1d(W, size=5, axis=1)

        # Terapkan mask
        V_est = W * M

        # ISTFT
        _, vocal = istft(V_est, sr, nperseg=N_FFT, noverlap=N_FFT - HOP_LENGTH,
                         window=WINDOW_TYPE)
        return vocal[:min_len].astype(np.float32)

    @staticmethod
    def separate_ss(mixed      : np.ndarray,
                    sr         : int   = SAMPLE_RATE,
                    alpha      : float = SS_ALPHA,
                    beta       : float = SS_BETA,
                    noise_frames: int  = SS_NOISE_FRAMES) -> np.ndarray:
        """
        Spektral subtraksi — fallback tanpa referensi musik.
        Estimasi noise dari N frame pertama.

        Args:
            mixed        : Audio campuran
            sr           : Sample rate
            alpha        : Over-subtraction factor
            beta         : Spectral floor
            noise_frames : Frame awal untuk estimasi noise

        Returns:
            vocal_estimated : np.ndarray float32
        """
        mixed = mixed.astype(np.float64)
        f, t, M = stft(mixed, sr, nperseg=N_FFT, noverlap=N_FFT - HOP_LENGTH,
                       window=WINDOW_TYPE)

        M_psd    = np.abs(M) ** 2
        # Estimasi PSD noise dari frame awal
        noise    = np.mean(M_psd[:, :noise_frames], axis=1, keepdims=True)

        # Over-subtraction + spectral floor
        V_psd    = np.maximum(M_psd - alpha * noise, beta * M_psd)
        gain     = np.sqrt(V_psd / (M_psd + 1e-10))
        gain     = np.clip(gain, 0.0, 1.0)

        # Temporal smoothing
        from scipy.ndimage import uniform_filter1d
        gain = uniform_filter1d(gain, size=5, axis=1)

        V_est    = gain * M
        _, vocal = istft(V_est, sr, nperseg=N_FFT, noverlap=N_FFT - HOP_LENGTH,
                         window=WINDOW_TYPE)
        return vocal[:len(mixed)].astype(np.float32)
