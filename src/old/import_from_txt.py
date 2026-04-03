from pathlib import Path


class TxtArtist:
    def __init__(self, name: str):
        self.name = name


class TxtSong:
    def __init__(self, title: str, artist_name: str):
        self.title = title
        self.artist = TxtArtist(artist_name)


def load_songs_from_txt(filename: str):
    file_path = Path(__file__).parent / filename

    if not file_path.exists():
        raise FileNotFoundError(f"Song file not found: {file_path}")

    songs = []

    with file_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if " - " not in line:
                print(f"⚠ Skipping invalid line {line_no}: {line}")
                continue

            artist, title = line.split(" - ", 1)

            songs.append(
                TxtSong(
                    title=title.strip(),
                    artist_name=artist.strip()
                )
            )

    return songs
