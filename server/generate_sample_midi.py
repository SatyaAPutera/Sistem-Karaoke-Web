"""
=================================================================
  GENERATE SAMPLE MIDI — Membuat file MIDI contoh untuk pengujian
  
  Menghasilkan file MIDI untuk vokal melodi lagu:
    - JVKE - golden hour.mid
    - Lady GaGa - Judas.mid
  di folder server/music/midi/
=================================================================
"""

import os
import mido

# Data melodi dasar untuk dicatat sebagai MIDI track
# format: (delta_time_ticks, note_num, velocity, type)
# ticks_per_beat = 480 (default)

def create_midi_for_golden_hour(output_path):
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)

    # Set tempo: 96 BPM -> 625,000 microseconds per beat
    track.append(mido.MetaMessage('set_tempo', tempo=625000, time=0))
    track.append(mido.MetaMessage('track_name', name='Vocal Melody', time=0))

    # Ticks per beat = 480
    # 96 BPM -> 1 beat = 625 ms. Maka 1 tick = 625 / 480 = 1.302 ms.
    # ms_to_ticks = ms / 1.302
    
    # Nada-nada dasar Golden Hour (dalam milidetik dari awal):
    # [15558] D4 (62) -> [16000] E4 (64) -> [16500] D4 (62)
    # [17507] D4 (62) -> [18000] E4 (64) -> [18500] F#4 (66) -> [19000] G4 (67)
    
    notes = [
        # (time_ms, note, duration_ms)
        (15558, 62, 400),
        (16000, 64, 400),
        (16500, 62, 800),
        
        (17507, 62, 400),
        (18000, 64, 400),
        (18500, 66, 400),
        (19000, 67, 400),
        (19500, 66, 400),
        (20000, 64, 400),
        (20500, 62, 800),
        
        (21416, 64, 500),
        (22000, 66, 500),
        (22500, 67, 500),
        (23000, 66, 500),
        (23500, 64, 500),
        (24000, 62, 800),
        
        (25054, 62, 400),
        (25500, 64, 400),
        (26000, 62, 700),
        (26819, 64, 300),
        (27200, 66, 600),
        (27903, 67, 300),
        (28300, 66, 800),
        
        (29278, 69, 500),
        (29800, 67, 500),
        (30300, 66, 500),
        (30800, 64, 500),
        (31300, 62, 1000),
        
        # Chorus (50s)
        (50109, 71, 400),
        (50600, 69, 400),
        (51100, 71, 400),
        (51600, 72, 400),
        (52100, 71, 450),
        (52600, 69, 450),
        (53100, 67, 450),
        (53600, 66, 450),
        (54100, 64, 800),
    ]

    last_ticks = 0
    for start_ms, note, duration_ms in notes:
        # Konversi start_ms dan duration_ms ke tick
        start_tick = int(start_ms / 1.302)
        duration_tick = int(duration_ms / 1.302)
        
        # Delta time untuk note_on
        delta_on = start_tick - last_ticks
        if delta_on < 0:
            delta_on = 0
            
        track.append(mido.Message('note_on', note=note, velocity=64, time=delta_on))
        
        # Delta time untuk note_off
        track.append(mido.Message('note_off', note=note, velocity=0, time=duration_tick))
        
        last_ticks = start_tick + duration_tick

    mid.save(output_path)
    print(f"[MIDI] File MIDI Golden Hour berhasil dibuat di {output_path}")

def create_midi_for_judas(output_path):
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)

    # Set tempo: 138 BPM -> 434,782 microseconds per beat
    track.append(mido.MetaMessage('set_tempo', tempo=434782, time=0))
    track.append(mido.MetaMessage('track_name', name='Vocal Melody', time=0))

    # 138 BPM -> 1 beat = 434.78 ms. 1 tick = 434.78 / 480 = 0.905 ms.
    
    notes = [
        # Intro
        (240, 67, 200),
        (500, 65, 200),
        (750, 67, 200),
        (1000, 65, 200),
        (1250, 67, 500),
        
        (2500, 64, 400),
        (3000, 62, 400),
        (3500, 60, 400),
        (4000, 62, 400),
        (4500, 64, 400),
        (5000, 62, 400),
        (5500, 60, 400),
        (6000, 59, 400),
        (6500, 57, 800),
        
        (7560, 67, 200),
        (7800, 65, 200),
        (8050, 67, 200),
        (8300, 65, 200),
        (8550, 67, 500),
    ]

    last_ticks = 0
    for start_ms, note, duration_ms in notes:
        start_tick = int(start_ms / 0.905)
        duration_tick = int(duration_ms / 0.905)
        
        delta_on = start_tick - last_ticks
        if delta_on < 0:
            delta_on = 0
            
        track.append(mido.Message('note_on', note=note, velocity=64, time=delta_on))
        track.append(mido.Message('note_off', note=note, velocity=0, time=duration_tick))
        
        last_ticks = start_tick + duration_tick

    mid.save(output_path)
    print(f"[MIDI] File MIDI Judas berhasil dibuat di {output_path}")

if __name__ == "__main__":
    os.makedirs("server/music/midi", exist_ok=True)
    create_midi_for_golden_hour("server/music/midi/JVKE - golden hour.mid")
    create_midi_for_judas("server/music/midi/Lady GaGa - Judas.mid")
