from src.settings import settings
from upath import UPath
from pathlib import Path
from src.debug import slog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

def create_temp_index():
    index_file = UPath(
        "smb://server.local/Shared/Music/.mp3_index.sqlite3",
        username=settings.smb_username,
        password=settings.smb_password,
    )

    local_index = Path("/tmp/.mp3_index.sqlite3")
    local_index.write_bytes(index_file.read_bytes())
    return local_index

def query_database(engine, query):

    sql, params = query
    with Session(engine) as session:
        result = session.execute(sql, params).fetchone()

    if result:
        return result[0]
    else:
        return None


def copy_file_to_destination(source, destination):
    if source.startswith("\\\\"):
        unc_path = source.replace("\\\\", "\\").replace("\\", "/").lstrip("/")
        smb_url = "smb://" + unc_path
        source_file = UPath(
            smb_url,
            username=settings.smb_username,
            password=settings.smb_password,
        )
    else:
        source_file = Path(source)

    dest_file = destination / source_file.name
    dest_file.write_bytes(source_file.read_bytes())
    print("Copied to:", dest_file)



def copy_songs_from_index(
    songs: list[tuple[str, str]],
    index_path,
    paste_destination: UPath | Path,
) -> None:

    slog(songs)
    slog(index_path)
    slog(paste_destination)

    if (isinstance(index_path, Path)):
        usable_index_path = index_path
    else:
        usable_index_path = create_temp_index()

    engine = create_engine(f"sqlite:///{usable_index_path}")

    with engine.connect():
        for artist, title in songs:
            query = (text("SELECT path FROM files WHERE artist = :artist AND title = :title"),
                    {"artist": artist, "title": title})
            
            path_from_db = query_database(engine, query)
            copy_file_to_destination(path_from_db, paste_destination)

    engine.dispose()