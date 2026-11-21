"""
Minimal UI translation: English source strings, Polish translation table.

The language is chosen once at start-up (before any window is built):
options.json "language" ("en" / "pl") if set, otherwise Polish when the
system locale is Polish, else English. Untranslated strings fall back to
English, so missing entries never break the UI.
"""
from __future__ import annotations

import json
import locale
import os

LANGUAGES = {"en": "English", "pl": "Polski"}
_language = "en"


def detect_language() -> str:
    try:
        from src.helpers import get_appdata_path
        with open(os.path.join(get_appdata_path(), "options.json"), encoding="utf-8") as f:
            chosen = json.load(f).get("language")
        if chosen in LANGUAGES:
            return chosen
    except Exception:
        pass
    try:
        from PyQt6.QtCore import QLocale
        if QLocale.system().language() == QLocale.Language.Polish:
            return "pl"
    except Exception:
        pass
    try:
        if (locale.getlocale()[0] or "").lower().startswith(("pl", "polish")):
            return "pl"
    except Exception:
        pass
    return "en"


def init_language(language: str | None = None) -> str:
    global _language
    _language = language if language in LANGUAGES else detect_language()
    return _language


def install_qt_translations(app) -> None:
    """Translate Qt's own texts (Save / Cancel buttons, file dialogs, ...)."""
    if _language == "en":
        return
    try:
        from PyQt6.QtCore import QLibraryInfo, QTranslator
        translator = QTranslator(app)
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if translator.load(f"qtbase_{_language}", path):
            app.installTranslator(translator)
    except Exception:
        pass


def current_language() -> str:
    return _language


def tr(text: str) -> str:
    if _language == "pl":
        return PL.get(text, text)
    return text


PL = {
    # --- claim / reminder types (table, toasts, phone notifications) ---
    "3-fold repetition": "Trzykrotne powtórzenie",
    "5-fold repetition": "Pięciokrotne powtórzenie",
    "50-move rule": "Reguła 50 posunięć",
    "75-move rule": "Reguła 75 posunięć",
    "Stalemate": "Pat",
    "Checkmate": "Mat",
    "Dead position": "Martwa pozycja",
    "Scoresheet change": "Zmiana blankietu",
    "2-fold repetition": "Dwukrotne powtórzenie",
    "45 moves": "45 posunięć",
    "Time control": "Kontrola czasu",
    "Low on time": "Mało czasu",
    "Flag fall": "Spadek flagi",
    "Sofia rule violation": "Naruszenie reguły zofijskiej",
    "First illegal move": "Pierwszy nieprawidłowy ruch",
    "Result verification": "Weryfikacja wyniku",
    "PGN download error": "Błąd pobierania PGN",
    "2-fold repetition (warning)": "Dwukrotne powtórzenie (ostrzeżenie)",
    "45 moves (warning)": "45 posunięć (ostrzeżenie)",

    # --- claims table ---
    "#": "#",
    "Timestamp": "Godzina",
    "Type": "Typ",
    "Board": "Szachownica",
    "Players": "Zawodnicy",
    "Move": "Ruch",
    "Ack": "Potw.",
    "Acknowledge": "Potwierdź",
    "Unacknowledge": "Cofnij potwierdzenie",
    "Filter by player, board or claim...": "Filtruj po zawodniku, szachownicy lub typie...",
    "Acknowledge selected ({n})": "Potwierdź zaznaczone ({n})",
    "Unacknowledge selected ({n})": "Cofnij potwierdzenie zaznaczonych ({n})",
    "Remove selected ({n})": "Usuń zaznaczone ({n})",
    "Acknowledge all of round {round} ({n})": "Potwierdź całą rundę {round} ({n})",
    "Remove all of round {round} ({n})": "Usuń całą rundę {round} ({n})",
    "Remove all {n} entries of round {round} from the table?": "Usunąć z tabeli wszystkie wpisy z rundy {round} ({n})?",

    # --- main window / buttons / status bar ---
    "Start Scan": "Rozpocznij skanowanie",
    "Stop Scan": "Zatrzymaj skanowanie",
    "Board Viewer": "Podgląd szachownic",
    "Clear Resolved": "Usuń potwierdzone",
    "Add Sources": "Dodaj źródła",
    "Sources ({count})": "Źródła ({count})",
    "{timestamp} Download:": "{timestamp} Pobieranie:",
    "Scan ({timestamp}):": "Skan ({timestamp}):",
    "Scanning PGN...": "Skanowanie PGN...",
    "Please Wait": "Proszę czekać",
    "Engine": "Silnik",
    "Engine ({time})": "Silnik ({time})",
    "Engine (waiting)": "Silnik (oczekuje)",
    "Warning": "Uwaga",
    "Scanning in Progress": "Trwa skanowanie",
    "Do you want to quit?": "Czy chcesz zamknąć program?",
    "PGN File(s) Not Found": "Nie znaleziono plików PGN",
    "Please enter at least one valid PGN source.": "Podaj co najmniej jedno prawidłowe źródło PGN.",
    "Akiba - User Manual": "Akiba - Instrukcja obsługi",
    "User Manual Not Found": "Nie znaleziono instrukcji",
    "The user documentation file could not be found.": "Nie znaleziono pliku z instrukcją obsługi.",
    "Error Reading Manual": "Błąd odczytu instrukcji",
    "Could not read the user documentation:": "Nie udało się odczytać instrukcji obsługi:",

    # --- menus ---
    "&Claims": "&Claimy",
    "&Alerts": "&Alerty",
    "&Reminders": "&Przypomnienia",
    "&Notifications": "Po&wiadomienia",
    "&Help": "Pomo&c",
    " moves": " posunięć",
    "Draw before move completed (Sofia rule)": "Remis przed ukończeniem posunięcia (reguła zofijska)",
    "Flag fall (00:00:00)": "Spadek flagi (00:00:00)",
    "Possible incorrect result": "Możliwy błędny wynik",
    "Low time - if less second than": "Mało czasu - poniżej sekund",
    "Possible 50-move (45 moves)": "Możliwa reguła 50 posunięć (45 posunięć)",
    "Possible threefold (2 fold)": "Możliwe trzykrotne powtórzenie (2 razy)",
    "Scoresheet change after move": "Zmiana blankietu po posunięciu",
    "Time control after move": "Kontrola czasu po posunięciu",
    "Enable Windows/Mac notifications": "Powiadomienia Windows/Mac",
    "Phone notifications (Pushover)...": "Powiadomienia na telefon (Pushover)...",
    "About": "O programie",
    "User Manual": "Instrukcja obsługi",
    "Open Log Folder": "Otwórz folder z logiem",
    "&Results": "&Wyniki",
    "Pla&yers": "&Zawodnicy",
    "Followed players...": "Śledzeni zawodnicy...",
    "Live board monitor...": "Monitor szachownic na żywo...",
    "Add to live board monitor": "Dodaj do monitora szachownic",
    "Akiba - Live board monitor": "Akiba - Monitor szachownic na żywo",
    "Search board or player...": "Szukaj szachownicy lub zawodnika...",
    "Add board": "Dodaj szachownicę",
    "Always on top": "Zawsze na wierzchu",
    "Pick a board above to follow it live: position, evaluation, clocks, repetitions and the 50-move counter.":
        "Wybierz szachownicę powyżej, aby śledzić ją na żywo: pozycja, ocena silnika, zegary, powtórzenia i licznik 50 posunięć.",
    "Open in Board Viewer": "Otwórz w podglądzie szachownic",
    "Stop monitoring this board": "Przestań śledzić tę szachownicę",
    "Evaluation": "Ocena",
    "Clocks": "Zegary",
    "Last move": "Ostatni ruch",
    "Material": "Materiał",
    "Repetition": "Powtórzenie pozycji",
    "Status": "Stan",
    "No capture / pawn move": "Bez bicia / ruchu pionem",
    "White to move": "ruch białych",
    "Black to move": "ruch czarnych",
    "in progress": "w toku",
    "Check": "Szach",
    "Insufficient material": "Niewystarczający materiał",
    "Akiba - Followed players": "Akiba - Śledzeni zawodnicy",
    "Search player...": "Szukaj zawodnika...",
    "Tick the players you want to follow. Players from the loaded PGN are listed; you can also add a name by hand.":
        "Zaznacz zawodników, których chcesz śledzić. Lista zawiera zawodników z wczytanych PGN; nazwisko możesz też dopisać ręcznie.",
    "Add": "Dodaj",
    "Followed: {n}": "Śledzeni: {n}",
    "Notify me about:": "Powiadamiaj o:",
    "Game finished": "Koniec partii",
    "Followed player: game finished": "Śledzony zawodnik: koniec partii",
    "Notifications go to the phone (Pushover) and to Windows notifications.":
        "Powiadomienia idą na telefon (Pushover) i jako powiadomienia Windows.",
    "Result": "Wynik",
    "board {board}, round {round}": "szachownica {board}, runda {round}",
    "Verify results with ChessManager": "Weryfikuj wyniki z ChessManager",
    "ChessManager tournaments...": "Turnieje w ChessManager...",
    "Verify results": "Weryfikuj wyniki",
    "Verifying...": "Weryfikacja...",
    "Compare transmission results with ChessManager": "Porównaj wyniki transmisji z ChessManager",
    "No games loaded - start the scan first.": "Brak wczytanych partii - najpierw rozpocznij skanowanie.",
    "Akiba - ChessManager tournaments": "Akiba - Turnieje w ChessManager",
    "One tournament link per line, e.g. https://www.chessmanager.com/tournaments/5437727486771200":
        "Jeden link do turnieju w linii, np. https://www.chessmanager.com/tournaments/5437727486771200",
    "Not a ChessManager tournament link: {url}": "To nie jest link do turnieju w ChessManager: {url}",
    "Tournaments: {n}": "Turnieje: {n}",
    "Akiba - Result verification (ChessManager)": "Akiba - Weryfikacja wyników (ChessManager)",
    "{n} results differ from ChessManager. Click a row to open the board.":
        "Wyniki niezgodne z ChessManager: {n}. Kliknij wiersz, aby otworzyć szachownicę.",
    "All results match ChessManager ({n} games checked).":
        "Wszystkie wyniki zgodne z ChessManager (sprawdzono partii: {n}).",
    "No game could be matched with ChessManager.": "Nie udało się dopasować żadnej partii do ChessManager.",
    "Round": "Runda",
    "Transmission": "Transmisja",
    "Problem": "Problem",
    "missing in transmission": "brak wyniku w transmisji",
    "different result": "inny wynik",
    "colours swapped in transmission": "zamienione kolory w transmisji",
    "not found in ChessManager": "nie znaleziono w ChessManager",
    "{n} finished games have no result in ChessManager yet.": "Zakończone partie bez wyniku w ChessManager: {n}.",
    "Could not read from ChessManager:": "Nie udało się odczytać danych z ChessManager:",
    "Games checked: {n}": "Sprawdzono partii: {n}",
    "Rounds: {rounds}": "Rundy: {rounds}",
    "Checked at {time}": "Sprawdzono o {time}",
    "{n} results differ from ChessManager": "Niezgodne wyniki: {n}",
    "Click a row to open the game on the board.": "Kliknij wiersz, aby otworzyć partię na szachownicy.",
    "All results match ChessManager": "Wszystkie wyniki są zgodne z ChessManager",
    "Could not read from ChessManager": "Nie udało się odczytać danych z ChessManager",
    "Results differ from ChessManager - click to see": "Wyniki niezgodne z ChessManager - kliknij, aby zobaczyć",
    "Last check: {time} - all results match": "Ostatnie sprawdzenie: {time} - wszystkie wyniki zgodne",
    "Language": "Język",
    "Restart Akiba to change the language.": "Uruchom Akibę ponownie, aby zmienić język.",

    # --- sources dialog ---
    "PGN Sources": "Źródła PGN",
    "Choose File": "Wybierz plik",
    "In progress: {ongoing}/{total} · Moves: {moves}": "Trwające: {ongoing}/{total} · Ruchy: {moves}",
    "{name}: {ongoing}/{total} in progress, {moves} moves": "{name}: trwające {ongoing}/{total}, ruchy: {moves}",
    "Other": "Inne",
    "Name (optional)": "Nazwa (opcjonalnie)",
    "Shown at the start of notifications, e.g. MP U20 Girls":
        "Pokazywana na początku powiadomień, np. MP K20",
    "Select File": "Wybierz plik",
    "PGN Files (*.pgn)": "Pliki PGN (*.pgn)",
    "Round:": "Runda:",
    "Apply": "Zastosuj",
    "OK": "OK",
    "Web(url)": "Internet (URL)",
    "Local": "Plik lokalny",

    # --- about ---
    "Akiba - About": "Akiba - O programie",
    "<b>Akiba</b> - Monitoring System for Chess Arbiters": "<b>Akiba</b> - system monitorowania partii dla sędziów szachowych",
    "Based on Chess Claim Tool by Athanasios Serntedakis 2019 ©": "Na podstawie Chess Claim Tool, Athanasios Serntedakis 2019 ©",

    # --- board viewer ---
    "Akiba - Board Viewer": "Akiba - Podgląd szachownic",
    "Search by player name or by board number ...": "Szukaj po nazwisku lub numerze szachownicy...",
    "Show only active games": "Pokaż tylko trwające partie",
    "Black: --:--:--": "Czarne: --:--:--",
    "White: --:--:--": "Białe: --:--:--",
    "Error loading PGN: {error}": "Błąd wczytywania PGN: {error}",
    "Last move ({time})": "Ostatni ruch ({time})",

    # --- phone notifications dialog ---
    "Akiba - Phone notifications": "Akiba - Powiadomienia na telefon",
    "Send notifications to phone / Apple Watch (Pushover)": "Wysyłaj powiadomienia na telefon / Apple Watch (Pushover)",
    "Pushover account": "Konto Pushover",
    "Your User Key (pushover.net dashboard)": "Twój User Key (strona pushover.net)",
    "API Token of the 'Akiba' application": "API Token aplikacji „Akiba”",
    "User key:": "User key:",
    "API token:": "API token:",
    "iOS: enable Critical Alerts for High priority in the Pushover app settings so alerts break through Silent / Focus mode.":
        "iOS: włącz Critical Alerts dla priorytetu High w ustawieniach aplikacji Pushover, aby powiadomienia przebijały się przez tryb cichy / skupienia.",
    "On": "Wł.",
    "Event": "Zdarzenie",
    "Priority": "Priorytet",
    "Message (preview)": "Wiadomość (podgląd)",
    "Edit...": "Edytuj...",
    "Devices": "Urządzenia",
    "Devices:": "Urządzenia:",
    "Refresh devices": "Odśwież urządzenia",
    "Loading...": "Wczytywanie...",
    "unknown - click Refresh devices": "nieznane - kliknij Odśwież urządzenia",
    "All devices": "Wszystkie urządzenia",
    "None": "Żadne",
    "Test": "Test",
    "Sending {name}...": "Wysyłanie: {name}...",
    "Sent. Check your phone / watch.": "Wysłano. Sprawdź telefon / zegarek.",
    "Enter the Pushover user key and API token first.": "Najpierw wpisz User key i API token z Pushover.",
    "Akiba - Message for {label}": "Akiba - Wiadomość: {label}",
    "Click a field to insert it where the cursor is (in Title or Message). Any other text is sent as typed.":
        "Kliknij pole, aby wstawić je w miejscu kursora (w tytule lub treści). Pozostały tekst zostanie wysłany tak, jak go wpiszesz.",
    "Title:": "Tytuł:",
    "Message:": "Treść:",
    "Preview on the phone": "Podgląd na telefonie",
    "Use this message for all events": "Użyj tej wiadomości dla wszystkich zdarzeń",
    "Lowest (silent)": "Najniższy (bez powiadomienia)",
    "Low (quiet)": "Niski (bez dźwięku)",
    "Normal": "Normalny",
    "High": "Wysoki",
    "Code (3FR)": "Kod (3FR)",
    "Event name": "Nazwa zdarzenia",
    "White": "Białe",
    "Black": "Czarne",
    "White - Black": "Białe - Czarne",
    "PGN download failed. Check the source URL / network.":
        "Nie udało się pobrać PGN. Sprawdź adres źródła / sieć.",
}
