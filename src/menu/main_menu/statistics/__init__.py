"""Statistics menu: shows how often each discovery fetcher has been invoked
and how often it actually found a usable album (see discovery_stats.py for
what "success" means)."""
from utils.discoveries.discoveries_manager import load_all_discovery_modules_metadata
from utils.discoveries.discovery_stats import load_discovery_stats


def show_discovery_stats():
    """Print each discovery module's invocation/success counts and success
    rate, in the user's configured order (enabled or not)."""
    modules = load_all_discovery_modules_metadata()  # [(module_id, display_name), ...]
    stats = load_discovery_stats()

    print("Discovery fetcher statistics\n")
    header = f"{'Fetcher':<28}{'Invocations':>13}{'Successes':>12}{'Success rate':>15}"
    print(header)
    print("-" * len(header))

    for module_id, display_name in modules:
        entry = stats.get(module_id, {})
        invocations = entry.get("invocations", 0)
        successes = entry.get("successes", 0)
        rate = f"{successes / invocations:.1%}" if invocations else "n/a"
        print(f"{display_name:<28}{invocations:>13}{successes:>12}{rate:>15}")
    print()
