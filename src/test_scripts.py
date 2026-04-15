import time
from utils.discoveries.wikipedia_fetcher import get_album_from_wikipedia

def test():

    album = get_album_from_wikipedia("Alanis Morissette", "You Oughta Know")
    print(album)

    time.sleep(10000)
