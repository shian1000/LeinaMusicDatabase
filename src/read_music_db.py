import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

class Artist(Base):
    __tablename__ = "artists"
    id = Column(Integer, primary_key=True)
    name = Column(String)

class Song(Base):
    __tablename__ = "songs"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    year = Column(Integer)
    language = Column(String)
    artist_id = Column(Integer, ForeignKey("artists.id"))
    nostalgic = Column(Integer)
    melancholic = Column(Integer)
    party = Column(Integer)
    artist = relationship("Artist")

# --- Make sure the database folder exists ---
def read_music_db():
    db_folder = os.path.join(os.path.dirname(__file__), "../database")
    os.makedirs(db_folder, exist_ok=True)

    # SQLite engine pointing to the database folder
    db_path = os.path.join(db_folder, "music.db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    # Create a session factory (other modules can import Session)
    Session = sessionmaker(bind=engine)

    # Export the main symbols
    __all__ = ["Base", "Artist", "Song", "engine", "Session"]

    if __name__ == "__main__":
        # Running this module directly will populate sample data once.
        session = Session()
        # Add some sample artists
        artist1 = Artist(name="The Beatles")
        artist2 = Artist(name="Adele")
        artist3 = Artist(name="Daft Punk")
        session.add_all([artist1, artist2, artist3])
        session.commit()

        # Add 10 sample songs
        songs = [
            Song(title="Hey Jude", year=1968, language="English", artist_id=artist1.id, nostalgic=8, melancholic=4, party=2),
            Song(title="Let It Be", year=1970, language="English", artist_id=artist1.id, nostalgic=9, melancholic=5, party=3),
            Song(title="Rolling in the Deep", year=2010, language="English", artist_id=artist2.id, nostalgic=7, melancholic=6, party=2),
            Song(title="Hello", year=2015, language="English", artist_id=artist2.id, nostalgic=6, melancholic=7, party=1),
            Song(title="Get Lucky", year=2013, language="English", artist_id=artist3.id, nostalgic=5, melancholic=2, party=9),
            Song(title="One More Time", year=2000, language="English", artist_id=artist3.id, nostalgic=6, melancholic=3, party=8),
            Song(title="Yesterday", year=1965, language="English", artist_id=artist1.id, nostalgic=9, melancholic=8, party=1),
            Song(title="Skyfall", year=2012, language="English", artist_id=artist2.id, nostalgic=7, melancholic=7, party=1),
            Song(title="Harder, Better, Faster, Stronger", year=2001, language="English", artist_id=artist3.id, nostalgic=6, melancholic=3, party=7),
            Song(title="Come Together", year=1969, language="English", artist_id=artist1.id, nostalgic=8, melancholic=4, party=3),
        ]
        session.add_all(songs)
        session.commit()
        print(f"Database created at {db_path} with 10 sample songs!")