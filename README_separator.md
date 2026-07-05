# 🎵 Voice Separator — Karaoke Project

**Modul:** Pemisahan Suara Vokal dari Campuran Vokal+Musik (Real-time)  
**PIC:** [nama anggota]  
**Bahasa:** Python 3.10+

---

## Latar Belakang & Posisi dalam Proyek

```
SERVER ──(stream musik)──────────────────────────────────┐
                                                          │
CLIENT MIC ──(rekam vokal+musik)──► VoiceSeparator ──► PitchDetector
                                         │
                              Wiener Filter (utama)
                              Spectral Subtraction (fallback)
```

Karena mic client merekam campuran suara vokal penyanyi **dan** musik dari speaker,
modul ini perlu memisahkan keduanya agar pitch detection hanya bekerja pada suara vokal.

---

## File

| File | Fungsi |
|------|--------|
| `voice_separator.py` | Modul utama — semua class DSP |
| `demo_live.py` | Demo real-time (mic + visualisasi) |
| `eval_separator.py` | Evaluasi kualitas dengan sinyal sintetis |
| `requirements.txt` | Dependency Python |

---

## Arsitektur Internal

```
voice_separator.py
├── VoiceSeparator          ← Interface utama (dipakai di aplikasi)
│   ├── receive_music()     ← Terima stream musik dari server
│   └── process()           ← Proses chunk mic, return vokal
│
├── OfflineVoiceSeparator   ← Untuk evaluasi / testing (non-realtime)
│   ├── separate_wiener()
│   └── separate_ss()
│
├── FrameProcessor          ← DSP per-chunk (stateful: overlap-add, smoothing)
│   ├── _wiener_mask()      ← PRIMARY: Wiener Filter
│   ├── _spectral_subtraction_mask()  ← FALLBACK: SS
│   └── _smooth_mask()      ← Temporal EMA smoothing
│
└── MusicReferenceBuffer    ← FIFO buffer sinkronisasi musik dari server
```

---

## Algoritma

### 1. Wiener Filter (Primary — saat referensi musik tersedia)

Digunakan saat audio musik referensi dari server sudah tersedia di buffer.

**Langkah:**
1. STFT pada audio campuran `M(f)` dan referensi musik `R(f)`
2. Estimasi PSD vokal:
   ```
   |V(f)|² = max( |M(f)|² − α·|R(f)|² , β·|M(f)|² )
   ```
3. Hitung Wiener mask:
   ```
   W(f) = |V(f)|² / ( |V(f)|² + |R(f)|² )
   ```
4. Terapkan mask: `V_est(f) = W(f) × M(f)`
5. ISTFT + temporal EMA smoothing (kurangi musical noise)

**Referensi:** Scalart & Filho, ICASSP 1996

**SDR hasil:** ~18 dB (vs input +9.9 dB tanpa separasi)

### 2. Spectral Subtraction (Fallback — tanpa referensi)

Digunakan saat musik dari server belum tiba (buffer underrun / jaringan lambat).

**Langkah:**
1. Estimasi PSD noise dari N frame pertama (asumssi = musik sebelum vokal masuk)
2. Over-subtraction:
   ```
   |V(f)|² = max( |M(f)|² − α·noise(f) , β·|M(f)|² )
   ```
3. Spectral gain: `G(f) = sqrt(|V(f)|² / |M(f)|²)`
4. Terapkan: `V_est(f) = G(f) × M(f)`

**Referensi:** Boll, IEEE Trans. ASSP 1979

**SDR hasil:** ~5.4 dB

### 3. Temporal Smoothing (EMA Mask)

Mencegah *musical noise* (suara bergelombang/berbunyi aneh pada output):
```
mask_smooth[t] = γ × mask[t−1] + (1−γ) × mask[t]
```
Default γ = 0.85. Nilai lebih tinggi = lebih halus tapi lebih lambat respon.

---

## Parameter Tuning

| Parameter | Default | Efek |
|-----------|---------|------|
| `WIENER_ALPHA` | 1.0 | Over-subtraction. Naik → musik lebih bersih, tapi vokal bisa distorsi |
| `WIENER_BETA` | 0.001 | Spectral floor. Turun → lebih bersih tapi ada silence artifact |
| `SS_ALPHA` | 2.0 | Agresivitas subtraksi noise |
| `SMOOTHING_GAMMA` | 0.85 | Temporal smoothing mask. 0=off, 0.95=very smooth |
| `BUFFER_SECONDS` | 0.5 | Max buffer lag musik dari server |

---

## Cara Menjalankan

```bash
# Install dependency sistem
sudo apt-get install portaudio19-dev   # Linux
brew install portaudio                  # macOS

pip install -r requirements.txt

# Demo live (butuh mic + musik dari speaker)
python demo_live.py

# Evaluasi kualitas (tanpa mic, sinyal sintetis)
python eval_separator.py
```

---

## Integrasi ke Aplikasi Karaoke

### Penggunaan dasar:
```python
from voice_separator import VoiceSeparator

sep = VoiceSeparator()

# Di thread penerima musik dari server (socket/WebRTC):
def on_music_received(audio_chunk: np.ndarray):
    sep.receive_music(audio_chunk)

# Di loop pemrosesan mic:
def process_mic_audio(mixed_audio: np.ndarray) -> np.ndarray:
    result = sep.process(mixed_audio)
    vocal  = result['vocal']    # Kirim ke pitch detector
    mode   = result['mode']     # 'wiener' atau 'ss'
    return vocal
```

### Integrasi dengan Pitch Detector:
```python
from voice_separator import VoiceSeparator
from pitch_detector import yin_pitch, SyllableSegmentor

sep       = VoiceSeparator()
segmentor = SyllableSegmentor()

def full_pipeline(mixed_audio, frame_idx):
    # Step 1: Separasi
    result = sep.process(mixed_audio)
    vocal  = result['vocal']

    # Step 2: Pitch detection
    pitch  = yin_pitch(vocal, 44100)

    # Step 3: Segmentasi suku kata
    rms    = np.sqrt(np.mean(vocal**2))
    syl    = segmentor.process(pitch, rms, frame_idx)

    return pitch, syl
```

### Integrasi jaringan (bagian Satya):
```python
# Server kirim musik → buffer
import socket

def receive_music_stream(sock: socket.socket, separator: VoiceSeparator):
    CHUNK = 1024
    while True:
        data = sock.recv(CHUNK * 4)  # float32 = 4 bytes
        if not data: break
        audio = np.frombuffer(data, dtype=np.float32)
        separator.receive_music(audio)

# Client kirim vokal ke server untuk scoring
def send_vocal_to_server(sock: socket.socket, vocal: np.ndarray):
    sock.sendall(vocal.tobytes())
```

---

## Metrik Evaluasi

| Metode | SDR | Keterangan |
|--------|-----|------------|
| Ideal (vokal asli) | ~90 dB | Batas atas teoritis |
| Wiener (ref bersih) | ~18 dB | Kondisi jaringan sempurna |
| Wiener (ref+noise) | ~17.9 dB | Robust terhadap noise jaringan |
| Tanpa separasi (raw) | ~10 dB | Baseline: mic langsung ke pitch detector |
| Spectral Subtraction | ~5.4 dB | Fallback saat referensi tidak tersedia |

SDR > 10 dB sudah cukup untuk pitch detection yang akurat.  
SDR > 15 dB memberikan hasil yang sangat baik.

---

## Keterbatasan & Rencana Pengembangan

| Limitasi | Solusi ke depan |
|----------|----------------|
| SS fallback SDR rendah jika musik berubah drastis | Adaptive noise tracking |
| Wiener butuh sinkronisasi timing musik-mic sempurna | Implementasi time alignment (cross-correlation) |
| Hanya mono | Tambahkan dukungan stereo |
| CPU-only | Bisa dipercepat dengan ONNX Runtime / cupy |
