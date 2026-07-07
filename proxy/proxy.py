"""
=================================================================
  KARAOKE PROXY SERVER (FastAPI)
  Port     : 8000
  Fungsi   : Bridge antara Browser dan Main Server
  
  Protokol yang dibuat (terlihat di Wireshark):
    Browser ──WebSocket──► Proxy :8000 ──UDP──► Main Server :8001
=================================================================
"""

import socket
import time
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Karaoke Proxy", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Konfigurasi tujuan UDP (Main Server) ──────────────────────────
DEST_UDP_IP   = "127.0.0.1"
DEST_UDP_PORT = 5004

# Socket UDP (persistent, tidak perlu bind port untuk send)
udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ── Statistik lalu lintas (untuk monitoring) ──────────────────────
stats = {
    "total_packets": 0,
    "total_bytes":   0,
    "active_connections": 0,
}


@app.get("/status")
def proxy_status():
    """Status proxy — berguna untuk monitoring dan demo Wireshark."""
    return {
        "status":             "ok",
        "dest_udp":           f"{DEST_UDP_IP}:{DEST_UDP_PORT}",
        "total_packets_sent": stats["total_packets"],
        "total_bytes_sent":   stats["total_bytes"],
        "active_connections": stats["active_connections"],
    }


@app.websocket("/ws/audio")
async def websocket_audio_proxy(websocket: WebSocket):
    """
    Terima chunk audio dari Browser via WebSocket,
    teruskan ke Main Server via UDP.
    
    Flow yang terlihat di Wireshark:
      TCP (WS) port 8000 ← Browser kirim audio
      UDP port 5004 → Server terima audio
    """
    await websocket.accept()
    stats["active_connections"] += 1

    client_info = websocket.client
    print(f"[Proxy] Browser terhubung: {client_info}")

    try:
        while True:
            # 1. Terima chunk audio dari browser (binary WebSocket)
            data = await websocket.receive_bytes()

            # 2. Teruskan ke Main Server via UDP
            udp_sock.sendto(data, (DEST_UDP_IP, DEST_UDP_PORT))

            # 3. Update statistik
            stats["total_packets"] += 1
            stats["total_bytes"]   += len(data)

    except WebSocketDisconnect:
        print(f"[Proxy] Browser terputus: {client_info}")
    except Exception as e:
        print(f"[Proxy] Error: {e}")
    finally:
        stats["active_connections"] -= 1


print("=" * 50)
print("  Karaoke Proxy Server v2.0")
print("  WS Audio : ws://localhost:8000/ws/audio")
print(f"  UDP Dest : udp://{DEST_UDP_IP}:{DEST_UDP_PORT}")
print("=" * 50)