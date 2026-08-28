# These are manual diagnostic / timing scripts, not automated tests.
# They run real MP3 imports and live network calls, so keep them out of the
# default `pytest` collection. Run them directly instead, e.g.:
#   python tests/manual/run_import_test.py
collect_ignore_glob = ["*.py"]
