from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./karaoke.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), nullable=False)
    email       = Column(String(100), unique=True, index=True)
    join_date   = Column(Date, default=datetime.utcnow)
    avatar_init = Column(String(2))
    sessions    = relationship("Session", back_populates="user")

class Song(Base):
    __tablename__ = "songs"
    id           = Column(Integer, primary_key=True, index=True)
    title        = Column(String(200), nullable=False)
    artist       = Column(String(100), nullable=False)
    genre        = Column(String(50))
    duration_sec = Column(Integer)
    play_count   = Column(Integer, default=0)
    year         = Column(Integer)
    audio_file   = Column(String(255))
    video_file   = Column(String(255))
    lyric_file   = Column(String(255))
    pitch_ref    = Column(Text)  

class Session(Base):
    __tablename__ = "sessions"
    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"))
    song_id        = Column(Integer, ForeignKey("songs.id"))
    started_at     = Column(DateTime, default=datetime.utcnow)
    ended_at       = Column(DateTime)
    final_score    = Column(Float)
    duration_sec   = Column(Integer)
    status         = Column(String(20), default="active")
    avg_latency    = Column(Float)
    avg_jitter     = Column(Float)
    packet_loss    = Column(Float)
    throughput     = Column(Float)
    network_scenario = Column(String(50))
    user           = relationship("User", back_populates="sessions")
    pitch_logs     = relationship("PitchLog", back_populates="session")
    network_stats  = relationship("NetworkStat", back_populates="session")

class PitchLog(Base):
    __tablename__ = "pitch_logs"
    id            = Column(Integer, primary_key=True)
    session_id    = Column(Integer, ForeignKey("sessions.id"), index=True)
    timestamp_ms  = Column(Integer)
    ref_hz        = Column(Float)
    user_hz       = Column(Float)
    note          = Column(String(5))
    is_match      = Column(Boolean)
    segment_score = Column(Float)
    syllable_idx  = Column(Integer)
    session       = relationship("Session", back_populates="pitch_logs")

class NetworkStat(Base):
    __tablename__ = "network_stats"
    id           = Column(Integer, primary_key=True)
    session_id   = Column(Integer, ForeignKey("sessions.id"), index=True)
    timestamp_ms = Column(Integer)
    latency_ms   = Column(Float)
    jitter_ms    = Column(Float)
    packet_loss  = Column(Float)
    throughput   = Column(Float)
    rtt          = Column(Float)
    session      = relationship("Session", back_populates="network_stats")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:    
        yield db
    finally: 
        db.close()