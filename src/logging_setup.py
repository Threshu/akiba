"""
Application-wide logging to a single file.

Everything goes to ONE file, ``akiba.log`` in the app data directory (next to
options.json), capped in size: when it grows past the cap the oldest half is
dropped in place, so there are never .log.1 / .log.2 copies.

Besides regular ``logging`` calls it records what normally leaves no trace in
a built .exe without a console:
  * unhandled exceptions on the main thread (incl. Qt slots) and worker threads,
  * Qt's own warnings / fatal messages,
  * hard native crashes (faulthandler stack dump),
  * a session start / clean exit marker, so a log ending without
    "session end" means the app crashed.

Modules just use ``logging.getLogger(__name__)``.
"""
from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import platform
import sys
import threading
from datetime import datetime

from src.helpers import get_appdata_path

LOG_FILENAME = "akiba.log"
MAX_BYTES = 5 * 1024 * 1024

_crash_file = None      # must stay open for faulthandler
_log_path = ""


class SingleFileHandler(logging.FileHandler):
    """FileHandler that keeps one capped file by trimming its oldest half."""

    def __init__(self, filename: str, max_bytes: int) -> None:
        super().__init__(filename, mode="a", encoding="utf-8")
        self.max_bytes = max_bytes

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.max_bytes > 0 and self.stream and self.stream.tell() >= self.max_bytes:
                self._trim()
        except Exception:
            pass  # logging must never take the app down
        super().emit(record)

    def _trim(self) -> None:
        self.stream.close()
        with open(self.baseFilename, "rb") as f:
            f.seek(-(self.max_bytes // 2), os.SEEK_END)
            f.readline()  # skip the partial line
            tail = f.read()
        with open(self.baseFilename, "wb") as f:
            f.write(b"--- older entries dropped (log size cap) ---\n")
            f.write(tail)
        self.stream = self._open()


def get_log_path() -> str:
    return _log_path or os.path.join(get_appdata_path(), LOG_FILENAME)


def setup_logging(level: int = logging.INFO) -> str:
    """Configure file logging and crash hooks. Idempotent. Returns the log path."""
    global _log_path
    if _log_path:
        return _log_path

    log_dir = get_appdata_path()
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, LOG_FILENAME)

    root = logging.getLogger()
    root.setLevel(level)
    file_handler = SingleFileHandler(path, MAX_BYTES)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"))
    root.addHandler(file_handler)
    if sys.stderr is not None:  # console only when started from a terminal
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
        root.addHandler(console)

    # python-chess logs every illegal move in a PGN as an error. Akiba reports
    # those itself (Alerts > First illegal move), so keep them out of the log.
    logging.getLogger("chess.pgn").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _log_path = path
    _install_excepthooks()
    _install_faulthandler(path)
    _install_qt_message_handler()
    atexit.register(_session_end)

    log = logging.getLogger("akiba")
    log.info("=" * 70)
    log.info("session start %s", datetime.now().isoformat(timespec="seconds"))
    log.info("python %s | %s %s | frozen=%s", sys.version.split()[0],
             platform.system(), platform.release(), getattr(sys, "frozen", False))
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        log.info("Qt %s | PyQt %s", QT_VERSION_STR, PYQT_VERSION_STR)
    except Exception:
        pass
    return path


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            pass


def _install_excepthooks() -> None:
    log = logging.getLogger("akiba.crash")
    previous = sys.excepthook

    def main_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        # With a custom excepthook PyQt6 no longer aborts on an exception
        # escaping a slot: it is logged and the app keeps running.
        log.critical("unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        _flush()

    def thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        log.critical("unhandled exception in thread %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        _flush()

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def _install_faulthandler(path: str) -> None:
    """Native stack dump on hard crashes, appended to the same file."""
    global _crash_file
    try:
        _crash_file = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        logging.getLogger("akiba").warning("could not enable faulthandler", exc_info=True)


def _install_qt_message_handler() -> None:
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return
    log = logging.getLogger("qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, context, message):
        level = levels.get(mode, logging.INFO)
        log.log(level, "%s", message)
        if level >= logging.CRITICAL:
            _flush()

    qInstallMessageHandler(handler)


def _session_end() -> None:
    logging.getLogger("akiba").info("session end (clean exit)")
    _flush()
