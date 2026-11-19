"""Songs' languages (songs.language).

Manage database -> "Unify language and country names": one checkbox list of
the stored languages and artists' origins whose spelling isn't the one kept
(languages.spelling_fixes()), all ticked.

Fill missing data -> "Languages": the get_language() modules (Settings ->
Discovery modules -> Languages) for the songs without one. The local ones (the
stored lyrics read by langdetect) first for every such song, then the sites
for at most max-songs-per-fetch of those still without a sure answer. Saved at
once: what a site says, and a reading of the lyrics with LANGUAGE_DETECT_SURE
in a language the artist isn't known not to sing. Asked at the end in one
list: the other readings, and for a song nothing knows the language nearly all
its artist's other songs have (LANGUAGE_ARTIST_MIN_SONGS / _SHARE) - ticked
when the artist sings that language in at least half its songs. A Serbian /
Croatian / Bosnian answer that neither the artist's country nor the year
settles (language_names.yugoslav_language(), any module's) is never saved nor
guessed: the owner is asked whether to type those by hand now or look them up
again once the artists' origins are known (owner's choice, 2026-11-19).
"""

from dataclasses import dataclass, replace

from config.constants import LANGUAGE_ARTIST_MIN_SHARE, LANGUAGE_ARTIST_MIN_SONGS
from utils.common.automation_settings import recent_misses, remember_misses
from utils.common.language_names import is_yugoslav, may_be_translation, yugoslav_language
from utils.common.output import escape, review_header, show
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.database.fetch_data_settings import load_max_songs_per_fetch
from utils.database.languages import (apply_spelling_fix, artist_languages, artist_share, has_language,
                                      other_song_languages, songs_missing_language, spelling_fixes)
from utils.discoveries.discoveries_manager import discover_language, load_discovery_modules
from utils.discoveries.discovery_settings import LANGUAGES
from utils.ui.prompts import ask_yes_no, stop_requested, tick_items, type_each, unattended_now


_ROWS = {"language": ("song", "songs"), "origin": ("artist", "artists")}


def _describe(fix) -> str:
    field, old, new, count = fix
    one, many = _ROWS[field]
    return f"{field}: {old!r}  ->  {new or '(not set)'}  ({count} {one if count == 1 else many})"


def unify_language_and_country_names() -> int:
    """Returns how many songs and artists were changed."""
    music_session, _ = open_and_set_global_database_sessions()
    fixes = [(field, *fix) for field in _ROWS for fix in spelling_fixes(music_session, field)]
    if not fixes:
        show("Every language and country is stored in its one spelling.")
        return 0

    review_header(f"REVIEW: {len(fixes)} spelling(s) of languages and countries to unify",
                  "Typos, case, ISO codes from mp3 tags, other separators, countries in one English form "
                  "('United Kingdom - England'), and origins that aren't a place cleared - Leina Radio "
                  "counts and searches by the exact name.")
    picked = tick_items("Tick the spellings to change (space toggles, enter confirms):",
                        fixes, _describe, checked=True)
    if picked is None:
        show("Aborted, nothing changed.")
        return 0

    changed = sum(apply_spelling_fix(music_session, field, old) for field, old, _, _ in picked)
    submit_global_database_session()
    show(f"Changed {changed} song(s) and artist(s).")
    return changed


@dataclass
class LanguageProposal:
    """A language to ask the owner about. confidence: the lyrics reading's,
    None for the artist's other songs' language."""
    song: object
    language: str
    from_lyrics: bool
    confidence: float | None
    artist_share: float
    artist_songs: int

    def facts(self) -> dict:
        return {"language": self.language, "from_lyrics": self.from_lyrics, "confidence": self.confidence,
                "artist_share": round(self.artist_share, 3), "artist_songs": self.artist_songs}

    @property
    def checked(self) -> bool:
        return self.artist_share >= (0.5 if self.from_lyrics else LANGUAGE_ARTIST_MIN_SHARE)

    def label(self) -> str:
        source = f"lyrics, {self.confidence:.2f}" if self.from_lyrics else "the artist's other songs"
        known = f"; the artist: {round(self.artist_share * self.artist_songs)}/{self.artist_songs} songs" \
            if self.artist_songs else ""
        note = "; the lyrics may be a translation" if self.from_lyrics and may_be_translation(
            self.song.title, self.language) else ""
        return f"{self.song.artist.name} - {self.song.title}  ->  {self.language}  ({source}{known}{note})"


def _proposal(song, match, counts: dict) -> LanguageProposal:
    share, songs = artist_share(counts.get(song.artist_id), match.result.language)
    return LanguageProposal(song, match.result.language, True, match.result.confidence, share, songs)


def _foreign(song, match, counts: dict) -> bool:
    """A reading of the lyrics in a language none of the artist's songs has
    (romaji read as Swahili, Belarusian as Russian), however sure langdetect is."""
    share, songs = artist_share(counts.get(song.artist_id), match.result.language)
    return match.result.from_song and songs >= LANGUAGE_ARTIST_MIN_SONGS and share == 0


def _country_rule(song, match):
    """A site's Serbian / Croatian / Bosnian answer under the owner's rule (the
    stored lyrics' reading has had it already, with the text)."""
    result = match.result
    if result.from_song or not is_yugoslav(result.language):
        return match
    decided = yugoslav_language("", song.year, song.artist.origin, other_song_languages(song))
    found = replace(result, language=decided) if decided else replace(result, needs_origin=True)
    return replace(match, result=found, sure=match.sure and decided is not None)


def _look_up(songs: list, modules: list, counts: dict, guesses: dict, asked: list, undecided: dict) -> list:
    """Ask `modules` about `songs`, saving the sure answers; an unsure one
    goes into `guesses` (song id -> match, a later sure one replaces it), one
    needing the artist's origin into `undecided` (song id -> (song, match)),
    each song asked into `asked`. Returns the songs still without a sure
    language and not undecided (not those a stop left)."""
    left = []
    for position, song in enumerate(songs, 1):
        if stop_requested():  # the automation: its time is up, or the app waits
            show("Stopped - what was found so far is kept.")
            break
        asked.append(song)
        search = discover_language(song, modules)
        match = search.match and _country_rule(song, search.match)
        if match is not None and match.result.needs_origin:
            undecided[song.id] = (song, match)
            guesses.pop(song.id, None)
            continue
        if match is not None and match.sure and not _foreign(song, match, counts):
            song.language = match.result.language
            guesses.pop(song.id, None)
            show(f"[{position}/{len(songs)}] {escape(song.artist.name)} - {escape(song.title)}: "
                 f"[green]{escape(song.language)}[/green] [dim]({escape(match.module_id)})[/dim]")
            continue
        if match is not None and song.id not in guesses:
            guesses[song.id] = match
        left.append(song)
    return left


def fill_missing_languages() -> int:
    """Returns how many songs got a language."""
    session, _ = open_and_set_global_database_sessions()
    modules = load_discovery_modules(LANGUAGES)
    if not modules:
        show("No language modules are on (Settings -> Discovery modules -> Languages).")
        return 0
    pending = songs_missing_language(session)
    if unattended_now():  # the automation: not the songs it left without one lately
        missed = recent_misses("languages")
        pending = [song for song in pending if song.id not in missed]
    if not pending:
        show("Every song has a language, or was looked up recently.")
        return 0
    show(f"{len(pending)} song(s) without a language.")
    counts = artist_languages(session)
    local = [module for module in modules if getattr(module[2], "LOCAL", False)]
    online = [module for module in modules if module not in local]
    guesses, undecided = {}, {}
    asked_locally, asked_online = [], []
    try:
        left = _look_up(pending, local, counts, guesses, asked_locally, undecided) if local else pending
        max_songs = load_max_songs_per_fetch()
        if online and left:
            if len(left) > max_songs:
                show(f"Asking the sites about the first {max_songs} of the {len(left)} left (see Settings to "
                     f"change this).")
            _look_up(left[:max_songs], online, counts, guesses, asked_online, undecided)
    except KeyboardInterrupt:
        show("\nStopped - what was found so far is kept.")
    asked = list({song.id: song for song in asked_locally + asked_online}.values())
    saved = sum(has_language(song) for song in asked)
    show(f"Saved the language of {saved} song(s).")
    # Only a song every module was asked about is given its artist's language, or remembered
    asked_all = asked_online if online else asked_locally
    saved += _review(_proposals(asked, guesses, counts, {song.id for song in asked_all}))
    saved += _ask_undecided(list(undecided.values()))
    submit_global_database_session()
    if unattended_now():
        remember_misses("languages", [song.id for song in asked_all if not has_language(song)])
    return saved


def _proposals(songs: list, guesses: dict, counts: dict, asked_all: set) -> list[LanguageProposal]:
    """The unsure readings, and for a song with none that every module was
    asked about (`asked_all`, ids) the language nearly all its artist's other
    songs have."""
    proposals = []
    for song in songs:
        if has_language(song):
            continue
        if song.id in guesses:
            proposals.append(_proposal(song, guesses[song.id], counts))
            continue
        artist = counts.get(song.artist_id)
        if not artist or song.id not in asked_all:
            continue
        language, _ = max(((name, count) for name, count in artist.items() if name != "songs"), key=lambda x: x[1])
        share, known = artist_share(artist, language)
        if known >= LANGUAGE_ARTIST_MIN_SONGS and share >= LANGUAGE_ARTIST_MIN_SHARE:
            proposals.append(LanguageProposal(song, language, False, None, share, known))
    return proposals


def _review(proposals: list[LanguageProposal]) -> int:
    if not proposals:
        return 0
    review_header(f"REVIEW: {len(proposals)} language(s) no source is sure of",
                  "Read from the lyrics without certainty, or in a language none of the artist's songs has; "
                  "or nothing found and the artist's other songs nearly all in one language.",
                  "Pre-selected: the artist sings that language in most of its songs.")
    picked = tick_items("Tick the languages to save (space toggles, enter confirms):", proposals,
                        LanguageProposal.label, checked=lambda p: p.checked, rule="languages_unsure",
                        facts=LanguageProposal.facts)
    if picked is None:
        show("Aborted, none of these saved.")
        return 0
    for proposal in picked:
        proposal.song.language = proposal.language
    show(f"Saved {len(picked)} more.")
    return len(picked)


def _ask_undecided(undecided: list) -> int:
    """The songs whose Serbian / Croatian / Bosnian lyrics neither the
    artist's country nor the year settle: typed by hand now, or left to be
    looked up again once the artists' origins are known. Returns how many
    were typed."""
    if not undecided:
        return 0
    review_header(f"{len(undecided)} song(s) in Serbian, Croatian or Bosnian whose artist's origin isn't known",
                  "Which of them it is goes by the artist's country and the song's year (before the country left "
                  "Yugoslavia: Serbo-Croatian) - neither is known for these.")
    for song, match in undecided:
        show(f"  {escape(song.artist.name)} - {escape(song.title)} ({song.year or 'year ?'}): read as "
             f"{escape(match.result.language)}")
    if not ask_yes_no("Type their languages by hand now? (no = look them up again later, once the artists' "
                      "origins are known - Artist actions -> Edit -> origin)"):
        show("Left without a language - looked up again next time.")
        return 0

    def save(item, typed):
        item[0].language = typed

    return type_each(undecided, lambda item: f"{escape(item[0].artist.name)} - {escape(item[0].title)} "
                                             f"({item[0].year or 'year ?'})",
                     "Language (empty = later):", save, default=lambda item: item[1].result.language)
