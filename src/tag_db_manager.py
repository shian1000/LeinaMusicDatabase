# src/tag_db_manager.py

import os
from pathlib import Path
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# --------------------
# Base & Engine
# --------------------

Base = declarative_base()

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "database" / "tag.db"

ENGINE = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=ENGINE)


# --------------------
# Models
# --------------------

class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class SongTag(Base):
    __tablename__ = "song_tags"

    id = Column(Integer, primary_key=True)
    song_id = Column(Integer, nullable=False)
    tag_id = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("song_id", "tag_id", name="uix_song_tag"),
    )


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


def delete_tag(tag_name: str):
    """
    Delete a tag and all its song references from tag.db.
    Case-insensitive.
    """
    tag_name = tag_name.strip().lower()
    session = get_tag_session()

    from sqlalchemy import func
    tag = session.query(Tag).filter(func.lower(Tag.name) == tag_name).first()

    if not tag:
        session.close()
        print(f"❌ Tag '{tag_name}' not found (case-insensitive).")
        return

    deleted_links = (
        session.query(SongTag)
        .filter(SongTag.tag_id == tag.id)
        .delete(synchronize_session=False)
    )

    session.delete(tag)
    session.commit()
    session.close()

    print(f"🗑️ Deleted tag '{tag.name}' and {deleted_links} links.")

