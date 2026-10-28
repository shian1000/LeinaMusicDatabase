from utils.ui.menu_utils import execute_menu_item
from menu.main_menu.enter_database import enter_database
from menu.main_menu.users import users_menu
from menu.main_menu.statistics import show_discovery_stats
from menu.main_menu.settings import settings_menu
from menu.main_menu.help import help_menu

def main_menu():
    action_map = {
        "Enter database": enter_database,
        "Users": users_menu,
        "Statistics": show_discovery_stats,
        "Settings": settings_menu,
        "Help": help_menu,
    }


    execute_menu_item("Main Menu", action_map, exit_label="Exit")