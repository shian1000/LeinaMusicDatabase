import time
from utils.discoveries.wikipedia_fetcher import get_album_from_wikipedia
import questionary

def test():

    questionary.text("What's your first name").ask()
    questionary.password("What's your secret?").ask()
    questionary.confirm("Are you amazed?").ask()

    questionary.select(
        "What do you want to do?",
        choices=["Order a pizza", "Make a reservation", "Ask for opening hours"],
    ).ask()

    questionary.rawselect(
        "What do you want to do?",
        choices=["Order a pizza", "Make a reservation", "Ask for opening hours"],
    ).ask()

    questionary.checkbox(
        "Select toppings", choices=["foo", "bar", "bazz"]
    ).ask()

    questionary.path("Path to the projects version file").ask()

    time.sleep(10000)
