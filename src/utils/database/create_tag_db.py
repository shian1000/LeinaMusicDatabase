import os
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# --------------------
# Database setup
# --------------------

Base = declarative_base()

DB_FOLDER = os.path.join(os.path.dirname(__file__), "../database")
os.makedirs(DB_FOLDER, exist_ok=True)

TAG_DB_PATH = os.path.join(DB_FOLDER, "tag.db")
ENGINE = create_engine(f"sqlite:///{TAG_DB_PATH}")

Session = sessionmaker(bind=ENGINE)


# --------------------
# Models
# --------------------

class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    def __repr__(self):
        return f"<Tag(name='{self.name}')>"


class SongTag(Base):
    __tablename__ = "song_tags"

    id = Column(Integer, primary_key=True)
    song_id = Column(Integer, nullable=False)
    tag_id = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("song_id", "tag_id", name="uix_song_tag"),
    )

    def __repr__(self):
        return f"<SongTag(song_id={self.song_id}, tag_id={self.tag_id})>"


# --------------------
# Helper functions
# --------------------

def get_or_create_tag(session, tag_name: str) -> Tag:
    """
    Fetch an existing tag or create it if it does not exist.
    """
    tag = session.query(Tag).filter_by(name=tag_name).first()
    if not tag:
        tag = Tag(name=tag_name)
        session.add(tag)
        session.commit()
    return tag


def add_tag_to_song(session, song_id: int, tag_name: str):
    """
    Attach a tag to a song ID (no foreign key validation).
    """
    tag = get_or_create_tag(session, tag_name)

    exists = (
        session.query(SongTag)
        .filter_by(song_id=song_id, tag_id=tag.id)
        .first()
    )

    if not exists:
        session.add(SongTag(song_id=song_id, tag_id=tag.id))
        session.commit()


def get_song_ids_by_tag(session, tag_name: str) -> list[int]:
    """
    Return a list of song IDs associated with a given tag.
    """
    results = (
        session.query(SongTag.song_id)
        .join(Tag, SongTag.tag_id == Tag.id)
        .filter(Tag.name == tag_name)
        .all()
    )

    return [row.song_id for row in results]


# --------------------
# Main
# --------------------

def main():
    Base.metadata.create_all(ENGINE)
    session = Session()

    # --- Example usage ---

    song_ids = get_song_ids_by_tag(session, "MyWedding")
    print("Songs tagged 'MyWedding':", song_ids)

    session.close()
    print(f"tag.db created at: {TAG_DB_PATH}")


if __name__ == "__main__":
    main()
