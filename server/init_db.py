from database import Base, engine, SessionLocal, User, Song
import json

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
            pitch_ref=json.dumps([
                {"t": 0,    "hz": 0},
                {"t": 1000, "hz": 293.66},
                {"t": 1500, "hz": 329.63},
            ])
        ),
        Song(
            title='Judas',
            artist='Lady Gaga',
            genre='pop',
            duration_sec=240,
            year=2011,
            audio_file=None, 
            video_file='music/media/[MMV] Gojo vs Sukuna  Jujutsu Kaisen x coldrain JUDAS.mp4',
            lyric_file='music/lyric/Lady GaGa - Judas.lrc',
            pitch_ref=json.dumps([
                {"t": 0,    "hz": 0},
                {"t": 1000, "hz": 293.66},
                {"t": 1500, "hz": 329.63},
            ])
        ),
        Song(
            title='Die With a Smile',
            artist='Lady Gaga, Bruno Mars',
            genre='pop',
            duration_sec=252,
            year=2024,
            audio_file='music/media/lady gaga karaoke ver.mp3', 
            video_file=None,
            lyric_file='music/lyric/Lady Gaga - Die With A Smile.en.lrc',
            pitch_ref=json.dumps([
                {"t": 0,    "hz": 0},
                {"t": 1000, "hz": 293.66},
                {"t": 1500, "hz": 329.63},
            ])
        )
    ]
    db.add_all(songs)
    db.commit()
    db.close()
    print("Database SQLite 'karaoke.db' berhasil diinisialisasi.")

if __name__ == "__main__":
    init()