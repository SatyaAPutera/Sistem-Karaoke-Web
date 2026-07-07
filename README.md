# 🎵 KaraokéOnline — Multimedia Network Project

Proyek aplikasi **Karaoke Online** interaktif dengan antarmuka web modern yang premium. Aplikasi ini dirancang khusus untuk memenuhi kebutuhan tugas mata kuliah **Jaringan Multimedia**, dengan fokus pada arsitektur lalu lintas jaringan yang tersegregasi secara jelas sehingga sangat mudah diuji dan dianalisis menggunakan tools network analyzer seperti **Wireshark**.

---

## 🚀 Fitur Utama

1. **Real-time Pitch Detector (Algoritma YIN):** mendeteksi nada suara penyanyi secara real-time langsung pada server dari input mic yang dikirimkan.
2. **Real-time Voice Separator (Wiener Filter & Spectral Subtraction):** memisahkan suara vokal penyanyi dari musik latar belakang (speaker) agar deteksi pitch tetap akurat.
3. **Interactive Scoring & Grading:** sistem penilaian berbasis *cents deviation* (penyimpangan frekuensi) dengan umpan balik visual real-time dan nilai akhir (S, A, B, C, D, F).
4. **LRC Lyrics Sync:** sinkronisasi lirik karaoke dinamis sesuai waktu jalannya musik/video.
5. **Interactive UI:** antarmuka berbasis Single Page Application (SPA) dengan sentuhan visual dark mode premium, partikel latar belakang dinamis, visualizer audio, dan papan peringkat (Leaderboard).

---

## 📡 Arsitektur Aliran Jaringan (Wireshark Testing)

Aplikasi ini menggunakan kombinasi berbagai protokol pada lapisan transport untuk mensimulasikan skenario jaringan multimedia nyata:

```
                  ┌──────────────────────────────────────────────┐
                  │              Browser (Frontend)              │
                  └──────┬──────────────────────┬─────────▲──────┘
                         │                      │         │
      WebSocket (Audio)  │                      │         │ WebSocket (Score)
      port 8000          │                      │         │ port 8001
                         ▼                      │         │
               ┌───────────────────┐            │         │
               │   Proxy Server    │            │ HTTP    │
               └─────────┬─────────┘            │ Range   │
                         │                      │ Requests│
               UDP       │                      │ port    │
               port 5004 │                      │ 8001    │
                         ▼                      │         │
               ┌───────────────────┐            │         │
               │    Main Server    │◄───────────┴─────────┘
               └───────────────────┘
```

### Detail Protokol & Port

| Aliran Data | Protokol | Port | Keterangan |
| :--- | :--- | :--- | :--- |
| **Mic Capture** | TCP / WebSocket | `8000` | Browser mengirim chunk audio mentah (PCM 16-bit) ke Proxy. |
| **Audio Relay** | **UDP** | `5004` | Proxy meneruskan chunk audio langsung ke Main Server. |
| **Score Stream** | TCP / WebSocket | `8001` | Server mengirim hasil deteksi pitch, note, dan score secara real-time ke Browser. |
| **REST API** | TCP / HTTP | `8001` | Browser melakukan request daftar lagu (`/api/songs`) & lirik (`/api/lyrics`). |
| **Video Stream**| TCP / HTTP (Range) | `8001` | Streaming video karaoke (`/media/*.mp4`) menggunakan HTTP Range requests. |

---

## 🛠️ Panduan Menjalankan Proyek (Windows)

Pastikan Anda membuka terminal di direktori utama: `c:\Users\Bagus\Desktop\Personal\Karaoke Online`

### 1. Persiapan Environment & Dependensi
Aktifkan Virtual Environment `.venv` Anda dan pasang pustaka yang diperlukan:

```bash
# Aktifkan virtual environment
.venv\Scripts\activate

# Install dependensi
pip install -r requirement.txt
```

### 2. Inisialisasi Database SQLite
Jalankan skrip berikut sekali saja untuk membuat database `karaoke.db` dan menginisialisasi lagu sampel:
```bash
python server/init_db.py
```

### 3. Jalankan Main Server (Port 8001)
Buka terminal baru, aktifkan `.venv`, lalu jalankan:
```bash
cd server
uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

### 4. Jalankan Proxy Server (Port 8000)
Buka terminal baru lagi, aktifkan `.venv`, lalu jalankan:
```bash
cd proxy
uvicorn proxy:app --host 127.0.0.1 --port 8000 --reload
```

### 5. Jalankan Aplikasi
Buka file `frontend/index.html` menggunakan browser pilihan Anda (direkomendasikan **Google Chrome** atau **Microsoft Edge** agar mic berjalan dengan baik tanpa konfigurasi SSL/HTTPS tambahan).

---

## 🔍 Cara Melakukan Analisis di Wireshark

1. Buka aplikasi **Wireshark**.
2. Pilih interface loopback capture (misal: **Adapter for loopback traffic-capture** atau **Npcap Loopback Adapter**).
3. Masukkan filter pencarian berikut pada kolom filter Wireshark:
   ```text
   tcp.port == 8000 or tcp.port == 8001 or udp.port == 5004
   ```
4. Mulai bernyanyi di aplikasi karaoke Anda.
5. Anda akan melihat visualisasi paket:
   * Paket **WebSocket (TCP)** pada port `8000` (aliran mic dari browser).
   * Paket **UDP** pada port `5004` (aliran mic yang diteruskan ke server).
   * Paket **WebSocket (TCP)** pada port `8001` (informasi skor real-time).
