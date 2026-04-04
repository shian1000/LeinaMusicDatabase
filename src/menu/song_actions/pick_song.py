import questionary

def pick_song(songs):
    songs_simple = [f"{artist} - {title}" for artist, title in songs]
    return questionary.select("Found more than one result:", choices=songs_simple).ask()