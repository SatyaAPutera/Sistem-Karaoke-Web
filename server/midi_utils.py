"""
=================================================================
  MIDI UTILS — Parser file MIDI ke pitch reference vokal
  
  Membaca file MIDI (.mid/.midi) dari folder server/music/midi/
  dan mengekstrak note event menjadi pitch reference format JSON:
    [{ "t": ms, "hz": hz, "note": note_name }, ...]
=================================================================
"""

import os
import mido
from pitch_utils import midi_to_hz, freq_to_note

def parse_midi_file(file_path: str) -> list:
    """
    Parse file MIDI dan ekstrak list event pitch reference.
    Mendukung file tipe 0 dan tipe 1.
    """
    if not os.path.exists(file_path):
        return []

    try:
        mid = mido.MidiFile(file_path)
    except Exception as e:
        print(f"[MIDI] Error membaca file {file_path}: {e}")
        return []

    pitch_ref = []
    
    # Mido mempermudah tracking waktu absolut dalam detik dengan mengonversi ticks
    # menggunakan tempo perubahan secara otomatis jika kita mengiterasi midi messages.
    # Namun, kita ingin membaca file secara sekuensial dengan waktu absolut.
    
    # Mari kita ubah setiap track menjadi rentang detik absolut.
    # Kita lacak semua event dari track yang berisi melodi vokal.
    # Biasanya, kita ambil track pertama yang memiliki note vokal terbanyak,
    # atau kita gabungkan seluruh track jika hanya ada satu instrumen vokal utama.
    
    # Cara paling aman dan kokoh adalah melacak pesan note_on secara berurutan.
    # Kita gunakan mido.merge_tracks(mid.tracks) untuk menggabungkan semua track menjadi satu timeline,
    # lalu mengiterasinya sambil melacak akumulasi waktu (dalam detik).
    
    # Konversi ticks ke seconds membutuhkan tempo (default 500000 = 120 BPM)
    ticks_per_beat = mid.ticks_per_beat
    current_tempo = 500000  # microseconds per beat (default)
    
    # Gabungkan semua event di semua track dengan waktu absolut (ticks)
    events = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if not msg.is_meta and msg.type in ['note_on', 'note_off']:
                events.append((abs_tick, msg))
            elif msg.is_meta and msg.type == 'set_tempo':
                events.append((abs_tick, msg))

    # Sortir event berdasarkan waktu absolut tick
    events.sort(key=lambda x: x[0])
    
    # Jalankan simulasi waktu (tick -> detik -> milidetik)
    abs_tick = 0
    elapsed_seconds = 0.0
    
    active_notes = {}  # midi_note_number -> start_time_seconds
    extracted_notes = []  # list of dict: {start: sec, end: sec, note: midi_note}

    for tick, msg in events:
        delta_tick = tick - abs_tick
        abs_tick = tick
        
        # Konversi delta_tick ke detik menggunakan tempo saat ini
        # seconds = ticks * (microseconds_per_beat / ticks_per_beat) / 1000000
        if delta_tick > 0:
            seconds_per_tick = (current_tempo / ticks_per_beat) / 1000000.0
            elapsed_seconds += delta_tick * seconds_per_tick
            
        if msg.is_meta and msg.type == 'set_tempo':
            current_tempo = msg.tempo
        elif msg.type == 'note_on' and msg.velocity > 0:
            # Note dimulai
            note = msg.note
            # Jika note sudah aktif, tutup dulu
            if note in active_notes:
                start_t = active_notes[note]
                extracted_notes.append({
                    "start": start_t,
                    "end": elapsed_seconds,
                    "note": note
                })
            active_notes[note] = elapsed_seconds
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            # Note selesai
            note = msg.note
            if note in active_notes:
                start_t = active_notes[note]
                extracted_notes.append({
                    "start": start_t,
                    "end": elapsed_seconds,
                    "note": note
                })
                del active_notes[note]

    # Tutup sisa note yang masih menggantung
    for note, start_t in active_notes.items():
        extracted_notes.append({
            "start": start_t,
            "end": elapsed_seconds,
            "note": note
        })

    # Sortir berdasarkan start time
    extracted_notes.sort(key=lambda x: x["start"])

    # Sekarang konversi note event ke format pitch reference interval (~100-200ms per point)
    # agar tracker bisa menggambar bar bersambung dengan mulus.
    # Kita lakukan sampling timeline setiap 100 milidetik.
    if not extracted_notes:
        return []
        
    max_time_sec = max(n["end"] for n in extracted_notes)
    # Tambah jeda sedikit di akhir
    total_duration_ms = int((max_time_sec + 1.0) * 1000)
    
    # Buat grid sampling setiap 100ms
    sample_rate_ms = 100
    timeline = {}  # t_ms -> (hz, note_name)

    # Isi timeline dengan notes
    for note_ev in extracted_notes:
        start_ms = int(note_ev["start"] * 1000)
        end_ms = int(note_ev["end"] * 1000)
        note_num = note_ev["note"]
        hz = midi_to_hz(note_num)
        note_info = freq_to_note(hz)
        note_name = note_info["name"] if note_info else ""
        
        # Isi setiap grid 100ms yang tumpang tindih dengan durasi note ini
        # Lakukan perataan (round) ke 100ms terdekat
        grid_start = (start_ms // sample_rate_ms) * sample_rate_ms
        grid_end = ((end_ms + sample_rate_ms - 1) // sample_rate_ms) * sample_rate_ms
        
        for t_ms in range(grid_start, grid_end, sample_rate_ms):
            timeline[t_ms] = (hz, note_name)

    # Konversi timeline dict ke list terurut [{t, hz, note}, ...]
    for t_ms in sorted(timeline.keys()):
        hz, note_name = timeline[t_ms]
        pitch_ref.append({
            "t": t_ms,
            "hz": round(hz, 2),
            "note": note_name
        })
        
    # Tambahkan titik silence (hz=0) di awal jika dimulai tidak pada t=0
    if pitch_ref and pitch_ref[0]["t"] > 0:
        pitch_ref.insert(0, {"t": 0, "hz": 0.0, "note": ""})

    print(f"[MIDI] Berhasil mengekstrak {len(pitch_ref)} titik pitch ref dari {os.path.basename(file_path)}")
    return pitch_ref


def parse_midi_segments(file_path: str) -> list:
    """
    Parse file MIDI dan kembalikan daftar segmen suku kata/note diskrit.
    Format:
        [{ "idx": 0, "start_ms": 1000, "end_ms": 1500, "hz": 293.66, "note": "D4" }, ...]
    """
    if not os.path.exists(file_path):
        return []

    try:
        mid = mido.MidiFile(file_path)
    except Exception as e:
        print(f"[MIDI Segments] Error membaca file {file_path}: {e}")
        return []

    ticks_per_beat = mid.ticks_per_beat
    current_tempo = 500000
    
    events = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if not msg.is_meta and msg.type in ['note_on', 'note_off']:
                events.append((abs_tick, msg))
            elif msg.is_meta and msg.type == 'set_tempo':
                events.append((abs_tick, msg))

    events.sort(key=lambda x: x[0])
    
    abs_tick = 0
    elapsed_seconds = 0.0
    
    active_notes = {}
    extracted_notes = []

    for tick, msg in events:
        delta_tick = tick - abs_tick
        abs_tick = tick
        
        if delta_tick > 0:
            seconds_per_tick = (current_tempo / ticks_per_beat) / 1000000.0
            elapsed_seconds += delta_tick * seconds_per_tick
            
        if msg.is_meta and msg.type == 'set_tempo':
            current_tempo = msg.tempo
        elif msg.type == 'note_on' and msg.velocity > 0:
            note = msg.note
            if note in active_notes:
                start_t = active_notes[note]
                extracted_notes.append({
                    "start": start_t,
                    "end": elapsed_seconds,
                    "note": note
                })
            active_notes[note] = elapsed_seconds
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            note = msg.note
            if note in active_notes:
                start_t = active_notes[note]
                extracted_notes.append({
                    "start": start_t,
                    "end": elapsed_seconds,
                    "note": note
                })
                del active_notes[note]

    for note, start_t in active_notes.items():
        extracted_notes.append({
            "start": start_t,
            "end": elapsed_seconds,
            "note": note
        })

    extracted_notes.sort(key=lambda x: x["start"])

    # Konversi ke format segmen milidetik diskrit
    segments = []
    for idx, note_ev in enumerate(extracted_notes):
        hz = midi_to_hz(note_ev["note"])
        note_info = freq_to_note(hz)
        segments.append({
            "idx": idx,
            "start_ms": int(note_ev["start"] * 1000),
            "end_ms": int(note_ev["end"] * 1000),
            "hz": round(hz, 2),
            "note": note_info["name"] if note_info else ""
        })
        
    print(f"[MIDI Segments] Berhasil mengekstrak {len(segments)} segmen suku kata dari {os.path.basename(file_path)}")
    return segments

