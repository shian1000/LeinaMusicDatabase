# src/tag_db_manager.py

import os
from sqlalchemy import (
    create_engine,
    CheckConstraint,
    Column,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from settings import Settings

# --------------------
# Base & Engine
# --------------------

Base = declarative_base()

BASE_DIR = Settings.database_dir
DB_PATH = BASE_DIR / "tag.db"

ENGINE = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=ENGINE)


# --------------------
# Models
# --------------------

class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    # TAG_KIND_MOOD (a global mood) or TAG_KIND_MARKER (e.g. "spellchecked"); tag migration 0003
    kind = Column(String, nullable=False, default="marker", server_default="marker")


class SongTag(Base):
    __tablename__ = "song_tags"

    id = Column(Integer, primary_key=True)
    song_id = Column(Integer, nullable=False)
    tag_id = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("song_id", "tag_id", name="uix_song_tag"),
    )


class AppliedSongChange(Base):
    """How far tag.db has replayed music.db's song_id_changes (tag migration
    0002; one row, see utils/database/song_references.py). A model too, so a
    tag.db made by create_all() for a fresh install has it."""
    __tablename__ = "applied_song_changes"

    id = Column(Integer, CheckConstraint("id = 1"), primary_key=True)
    last_change_id = Column(Integer, nullable=False)


# --------------------
# Public API
# --------------------

def create_tag_db():
    """
    Create tag.db with all tables.
    """
    os.makedirs(DB_PATH.parent, exist_ok=True)
    Base.metadata.create_all(ENGINE)


def get_tag_session():
    """
    Get a new SQLAlchemy session for tag.db.
    """
    return SessionLocal()

