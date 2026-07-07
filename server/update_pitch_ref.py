"""
=================================================================
  UPDATE PITCH REF — Update kolom pitch_ref di database
  
  Jalankan: python server/update_pitch_ref.py
  (dari root folder proyek)
=================================================================
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))

from database import SessionLocal, Song
from pitch_ref_data import get_pitch_ref, PITCH_REF_MAP

from midi_utils import parse_midi_file
from main import _find_midi_file

def update():
    db = SessionLocal()
    try:
        songs = db.query(Song).all()
        updated = 0

        for song in songs:
            # 1. Coba MIDI terlebih dahulu
            ref_data = []
            source = "none"
            
            midi_path = _find_midi_file(song)
            if midi_path:
                ref_data = parse_midi_file(str(midi_path))
                if ref_data:
                    source = f"MIDI ({midi_path.name})"
            
            # 2. Coba curated fallback
            if not ref_data:
                ref_data = get_pitch_ref(song.id)
                if ref_data:
                    source = "Curated Data"
                    
            if ref_data:
                # Simpan sebagai JSON array [{t, hz}, ...]
                song.pitch_ref = json.dumps([
                    {"t": ev["t"], "hz": ev["hz"]}
                    for ev in ref_data
                ])
                print(f"  OK [{song.id}] {song.title} -- {len(ref_data)} titik ({source})")
                updated += 1
            else:
                print(f"  SKIP [{song.id}] {song.title} -- tidak ada data referensi")

        db.commit()
        print(f"\nSelesai! {updated} lagu diperbarui pitch reference-nya.")
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 55)
    print("  Update Pitch Reference Database")
    print("=" * 55)
    update()
