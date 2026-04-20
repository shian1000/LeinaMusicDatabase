import questionary
import time
from utils.database.database_getter import get_artists_from_db_session, extract_db_object_info
from utils.debug import slog, mlog
from utils.database.database_sessions import submit_global_database_session
from utils.database.database_management import merge_artists_in_db
from utils.database.datatables import artist_categories
from rich import print
from questionary import Style

def common_artist_menu(q0 = None, q1 = None, on_confirm = None):

    query = input("What querry do you wish to search for: ")

    if query == "":
        return

    # artists_objects = get_artists_from_db_session(artist_categories[0], query, aggresive_search=True)
    # slog(artists_objects)
    # mlog(extract_db_object_info(artists_objects, f"{artist_categories[0]}, {artist_categories[1]}"))
    # slog(len(artists_objects))

    # if len(artists_objects)>1:
    #     artists_names = extract_db_object_info(artists_objects, "artist name")
    #     artists_names = [item for t in artists_names for item in t]+["Back"]
    #     if(q0):
    #         from_names = questionary.select(q0_msg, choices=artists_names, style=q0_sty).ask()
    #         if from_names == "Back":
    #             return
    #     if(q1):
    #         to_names = questionary.select(q1_msg, choices=artists_names, style=q1_sty).ask()
    #         if to_names == "Back":
    #             return
    #         from_obj = artists_objects[artists_names.index(from_names)]
    #         to_obj = artists_objects[artists_names.index(to_names)]
    #         if from_obj == to_obj:
    #             print("Selected the same artists")
    #changed here to check against index, not name
    artists_objects = get_artists_from_db_session(artist_categories[0], query, aggresive_search=True)
    slog(artists_objects)
    mlog(extract_db_object_info(artists_objects, f"{artist_categories[0]}, {artist_categories[1]}"))
    slog(len(artists_objects))

    if len(artists_objects) > 1:
        artists_names = extract_db_object_info(artists_objects, "artist name")
        artists_names = [item for t in artists_names for item in t]

        choices = [questionary.Choice(title=name, value=i) for i, name in enumerate(artists_names)]
        choices.append(questionary.Choice(title="Back", value=-1))

        if q0:
            index0 = questionary.select(q0[0], choices=choices, style=q0[1]).ask()
            if index0 == -1:
                return
            elif on_confirm:
                obj0 = artists_objects[index0]
                on_confirm(obj0)
        if q1:
            index1 = questionary.select(q1[0], choices=choices, style=q1[1]).ask()
            if index1 == -1:
                return
            obj0 = artists_objects[index0]
            obj1 = artists_objects[index1]
            if obj0 == obj1:
                print("Selected the same artists")
            elif on_confirm:
                on_confirm(obj0, obj1)
    elif len(artists_objects) == 1:
        print("Only one artist found")

def merge_artist_menu_new():
    #STEP1 - Dowiedz się, czego użytkownik szuka
    query = input("What querry do you wish to search for: ")
    artists_objects = get_artists_from_db_session(artist_categories[0], query, aggresive_search=True)

    #STEP2 - Sprawdź, czy 0 albo 1, wyświetl ew. błąd

    #STEP3 - Wybierz z listy - zrobić osobną funkcję na to

    #STEP3 - Wybierz z listy znowu

    #STEP4 - Porównaj, czy obiekty te same - ew. wyświetl błąd

    #STEP5 - handle_merge

def merge_artists_menu():
    red = Style([('question', 'fg:red bold')])
    green = Style([('question', 'fg:green bold')])
    q0 = ("Select the artist you want to merge from (gets deleted from db)", red)
    q1 = ("Select the artist you want to merge to (stays in db)", green)

    def handle_merge(from_obj, to_obj):
        confirm = questionary.confirm(
            f"You are about to merge all the songs from {from_obj.name} into {to_obj.name}. Do you wish to proceed?"
        ).ask()
        if confirm:
            print("Merging songs")
            merge_artists_in_db(from_obj, to_obj)
            submit_global_database_session()

    return common_artist_menu(q0, q1, on_confirm=handle_merge)


def divide_artists_menu():
    q0 = ("Select the artist you want to divide", None)
    common_artist_menu(q0)
    print()