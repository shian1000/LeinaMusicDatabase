# tests/test_scripts.py is an explicit scratch/manual-testing file (its own
# docstring: "meant to be a mess... nothing depends on this script"), not a
# regression test -- its body is entirely commented out except a
# `time.sleep(10000)`. Exclude it from collection: importing it pulls in
# `menu.song_actions`, which hits a real circular import
# (src/menu/song_actions/__init__.py <-> src/menu/main_menu/...), so letting
# pytest try to collect it just fails the whole run for a file that was never
# meant to assert anything.
collect_ignore = ["test_scripts.py"]
