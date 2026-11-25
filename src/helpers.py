import enum
import os
import platform
import ssl
import sys
from functools import lru_cache


APP_NAME = "Akiba"


def write_file_atomic(path: str, data: bytes) -> None:
    """
    Replace a file in one step (temp file + os.replace).

    PGN files are re-read every few seconds by other threads; a reader must
    never see a half-written file - a truncated PGN turns into phantom games
    with '?' headers and false mate / illegal move alerts.
    """
    import time
    tmp = path + ".tmp"
    with open(tmp, "wb") as file:
        file.write(data)
    for _ in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)  # Windows: a reader still has the file open
    with open(path, "wb") as file:  # last resort
        file.write(data)
    try:
        os.remove(tmp)
    except OSError:
        pass


def stable_game_id(game_or_headers) -> str:
    """
    Identifier of a game from its PGN headers: Event|Site||Round|White|Black.

    Date is left empty on purpose: LiveChess re-exports finished rounds with
    the current day, which would make every old game look like a new one.
    """
    headers = getattr(game_or_headers, "headers", game_or_headers)
    return "|".join("" if key == "Date" else headers.get(key, "")
                    for key in ("Event", "Site", "Date", "Round", "White", "Black"))


def is_phantom_game(game) -> bool:
    """A 'game' without players - a fragment of a truncated PGN, not a real game."""
    white = str(game.headers.get("White", "") or "").strip()
    black = str(game.headers.get("Black", "") or "").strip()
    return white in ("", "?") and black in ("", "?")


def get_engine_path() -> str:
    """
    Absolute path of the Stockfish executable.

    Order: engine/stockfish.exe next to the program (lets the arbiter drop in
    a newer engine), the engine bundled inside the .exe, then the project's
    engine/ folder when running from source. An absolute path is required:
    Windows does not start "engine/stockfish.exe" given as a relative path.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "engine", "stockfish.exe"))
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "engine", "stockfish.exe"))
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(project_root, "engine", "stockfish.exe"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[-1]


@lru_cache(maxsize=None)
def get_ssl_context(cafile: str) -> ssl.SSLContext:
    """
    Cached SSL context for HTTPS downloads.

    urlopen(..., cafile=...) was removed in Python 3.13; pass
    context=get_ssl_context(path) instead. The CA bundle is read once.
    """
    return ssl.create_default_context(cafile=cafile)


def get_board_number(game) -> str:
    """
    Board identifier shown in the table and in notifications.

    Uses the Board header. Many tournament PGNs have no Board header and
    encode it in Round as ROUND.BOARD (e.g. "3.4"); then only the board
    part ("4") is returned. Falls back to "-".
    """
    board = str(game.headers.get("Board") or "").strip()
    if board and board not in ("None", "?"):
        return board

    rnd = str(game.headers.get("Round") or "").strip()
    if not rnd or rnd in ("None", "?"):
        return "-"
    if "." in rnd:
        parts = [part for part in rnd.split(".") if part]
        if parts:
            return parts[-1]
    return rnd


def resource_path(relative_path: str) -> str:
    """
    Return the absolute path to a resource file.

    This function works correctly both when running the program normally
    and when packaged with PyInstaller. It also handles special cases
    for CSS files, icons, and certificate files based on the project's directory structure.

    Args:
        relative_path (str): Path to a resource file (e.g., "main.css", "icons/logo.png").

    Returns:
        str: Absolute path to the resource.
    """

    # PyInstaller "frozen" mode: resources are stored in sys._MEIPASS
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
        
        # Icons are stored in icons/ subfolder when frozen
        if relative_path.endswith((".png", ".jpg", ".jpeg", ".ico", ".icns")):
            base_path = os.path.join(base_path, "icons")
        
        # Certificate files (.pem) are stored in cert/ subfolder when frozen
        elif relative_path.endswith(".pem"):
            base_path = os.path.join(base_path, "cert")
        
        # Beacon config JSON is in local/ subfolder when frozen
        elif relative_path.endswith(".json"):
            base_path = os.path.join(base_path, "local")
    
    else:
        # Directory where helpers.py is located (src/)
        current_dir = os.path.abspath(os.path.dirname(__file__))

        # CSS files are stored in src/views/ - need to handle both "main.css" and paths from root
        if relative_path.endswith(".css"):
            base_path = os.path.join(current_dir, "views")
        
        # Icons are stored in icons/ (one level above src/)
        elif relative_path.endswith((".png", ".jpg", ".jpeg", ".ico", ".icns")):
            base_path = os.path.abspath(os.path.join(current_dir, "..", "icons"))

        # Certificate files (.pem) are stored in cert/ at root level
        elif relative_path.endswith(".pem"):
            return os.path.abspath(os.path.join(current_dir, "..", "cert", "cacert.pem"))

        # Markdown documentation files are stored at root level
        elif relative_path.endswith(".md"):
            base_path = os.path.abspath(os.path.join(current_dir, ".."))

        # Beacon config JSON is in local/ folder (development only)
        elif relative_path.endswith(".json"):
            # Handle both "config_url.json" and "local/config_url.json"
            if not relative_path.startswith("local/"):
                return os.path.abspath(os.path.join(current_dir, "..", "local", relative_path))
            else:
                return os.path.abspath(os.path.join(current_dir, "..", relative_path))

        # Default: use the directory of helpers.py
        else:
            base_path = current_dir

    # Extract just the filename from relative_path (handle both "main.css" and "src/views/main.css")
    filename = os.path.basename(relative_path)
    
    return os.path.join(base_path, filename)


def get_appdata_path() -> str:
    """
    Return the directory where the application should store its data.

    The location depends on the operating system:
        - Windows → %APPDATA%/Akiba
        - macOS   → ~/Library/Application Support/Akiba
        - Linux   → ~/.local/share/Akiba

    Returns:
        str: Absolute path to the application's data directory.
    """

    system = platform.system()

    if system == "Darwin":  # macOS
        base_path = os.path.join(os.getenv("HOME"), "Library", "Application Support")

    elif system == "Windows":
        base_path = os.getenv("APPDATA")

    else:
        # Linux, BSD, and other Unix-like systems
        base_path = os.path.join(os.getenv("HOME"), ".local", "share")

    return os.path.join(base_path, APP_NAME)


class Status(enum.Enum):
    """
    Enum representing different application states.

    Used for UI updates, worker status reporting, and general state handling.
    """
    OK = 1
    ERROR = 2
    STOP = 3
    ACTIVE = 4
    WAIT = 5
    INACTIVE = 6
