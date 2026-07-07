import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import Base, engine, SessionLocal, User, Song
from pitch_ref_data import pitch_ref_to_json

def init():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = [
        User(name='Chandra Ananda',  email='chandra@example.com', avatar_init='CA'),
        User(name='Bagus Ajie',      email='bagus@example.com', avatar_init='BA'),
        User(name='Satya Adnyana',   email='satya@example.com', avatar_init='SA'),
    ]
    db.add_all(users)
    db.commit()

    songs = [
        Song(
            title='Golden Hour',
            artist='JVKE',
            genre='pop',
            duration_sec=210,
            year=2022,
            audio_file='music/media/JVKE - golden hour (instrumental).mp3', 
            video_file='music/media/Golden Hour Waguri Clip.mp4',
            lyric_file='music/lyric/JVKE - golden hour.lrc',
            pitch_ref='Golden Hour.mid',   # song_id=1
        ),
        Song(
            title='Judas',
            artist='Lady Gaga',
            genre='pop',
            duration_sec=240,
            year=2011,
            audio_file='music/media/lady gaga karaoke ver.mp3', 
            video_file='music/media/[MMV] Gojo vs Sukuna  Jujutsu Kaisen x coldrain JUDAS.mp4',
            lyric_file='music/lyric/Lady GaGa - Judas.lrc',
            pitch_ref='Lady GaGa - Judas.mid',   # song_id=2
        ),
        Song(
            title='Secret Door',
            artist='Arctic Monkeys',
            genre='pop',
            duration_sec=200,
            year=2014,
            audio_file='music/media/Secret Door.mp3', 
            video_file='music/media/Secret door vid.mp4',
            lyric_file='music/lyric/Arctic Monkeys - Secret Door.lrc',
            pitch_ref=pitch_ref_to_json(3),   # song_id=3
        ),
        Song(
            title='Counting Stars',
            artist='OneRepublic',
            genre='pop',
            duration_sec=257,
            year=2013,
            audio_file='music/media/33 - Counting Stars (Instrumental).mp3', 
            video_file=None,
            lyric_file='music/lyric/COUNTING STARS - ONEREPUBLIC - NATIVE - 2013 (1).lrc',
            pitch_ref='OneRepublic-Counting Stars.mid',   # song_id=4
        ),
    ]
    db.add_all(songs)
    db.commit()
    db.close()
    
    # Tampilkan jumlah titik pitch ref per lagu
    from pitch_ref_data import PITCH_REF_MAP
    for sid, data in PITCH_REF_MAP.items():
        print(f"  Song {sid}: {len(data)} titik pitch reference")
    print("Database SQLite 'karaoke.db' berhasil diinisialisasi.")

if __name__ == "__main__":
    init()