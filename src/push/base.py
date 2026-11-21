"""
Provider-independent part of phone push notifications.

Everything an arbiter configures per event type (on/off, priority, title and
body templates) lives here, so adding another provider (ntfy, Bark, ...) only
means implementing ``PushProvider.send``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, Tuple

from src.models.events import ClaimType


class PushError(Exception):
    """Raised by a provider when a message could not be delivered."""


# Priorities follow Pushover's scale; other providers map them onto their own.
PRIORITY_LOWEST = -2     # no alert at all
PRIORITY_LOW = -1        # quiet
PRIORITY_NORMAL = 0
PRIORITY_HIGH = 1        # bypasses quiet hours
# Pushover's priority 2 (emergency) repeats until acknowledged; deliberately
# not used: alerts must arrive without having to confirm them.

PRIORITY_LABELS = {
    PRIORITY_LOWEST: "Lowest (silent)",
    PRIORITY_LOW: "Low (quiet)",
    PRIORITY_NORMAL: "Normal",
    PRIORITY_HIGH: "High",
}

ERR_PGN = "ERR_PGN"

# code -> (label, default priority), in the order shown in the settings dialog.
EVENT_TYPES: Dict[str, Tuple[str, int]] = {
    "3FR": ("3-fold repetition", PRIORITY_HIGH),
    "5FR": ("5-fold repetition", PRIORITY_HIGH),
    "50M": ("50-move rule", PRIORITY_HIGH),
    "75M": ("75-move rule", PRIORITY_HIGH),
    "STALEMATE": ("Stalemate", PRIORITY_NORMAL),
    "MATE": ("Checkmate", PRIORITY_NORMAL),
    "DEAD": ("Dead position", PRIORITY_NORMAL),
    "ILLEGAL": ("First illegal move", PRIORITY_HIGH),
    "FLAG": ("Flag fall", PRIORITY_HIGH),
    "SOFIA": ("Sofia rule violation", PRIORITY_NORMAL),
    "RESULT": ("Result verification", PRIORITY_NORMAL),
    "2FR": ("2-fold repetition (warning)", PRIORITY_NORMAL),
    "45M": ("45 moves (warning)", PRIORITY_NORMAL),
    "SCORESHEET": ("Scoresheet change", PRIORITY_NORMAL),
    "TC": ("Time control", PRIORITY_NORMAL),
    "LOWTIME": ("Low on time", PRIORITY_NORMAL),
    ERR_PGN: ("PGN download error", PRIORITY_HIGH),
    "GAME_END": ("Followed player: game finished", PRIORITY_HIGH),
}

CLAIM_TYPE_TO_CODE: Dict[ClaimType, str] = {
    ClaimType.THREEFOLD: "3FR",
    ClaimType.FIVEFOLD: "5FR",
    ClaimType.FIFTY_MOVES: "50M",
    ClaimType.SEVENTYFIVE_MOVES: "75M",
    ClaimType.STALEMATE_CLAIM: "STALEMATE",
    ClaimType.CHECKMATE_CLAIM: "MATE",
    ClaimType.DEAD_POSITION_CLAIM: "DEAD",
    ClaimType.ILLEGAL_MOVE: "ILLEGAL",
    ClaimType.FLAG_FALL_REMINDER: "FLAG",
    ClaimType.SOFIA_RULE: "SOFIA",
    ClaimType.MISMATCHED_RESULT: "RESULT",
    ClaimType.TWO_FOLD_WARNING: "2FR",
    ClaimType.FORTYFIVE_MOVES_WARNING: "45M",
    ClaimType.SCORESHEET_REMINDER: "SCORESHEET",
    ClaimType.TIMECONTROL_REMINDER: "TC",
    ClaimType.LOW_TIME_REMINDER: "LOWTIME",
}

DEFAULT_TITLE = "{code} #{board}"
DEFAULT_BODY = "{white} - {black}\n{move}"
ERR_PGN_TITLE = "{type}"
GAME_END_TITLE = "#{board} {result}"
GAME_END_BODY = "{white} - {black}"
ERR_PGN_BODY = "PGN download failed. Check the source URL / network."

# Placeholders offered in the template editor: (key, label shown to the arbiter)
TEMPLATE_FIELDS = (
    ("code", "Code (3FR)"),
    ("type", "Event name"),
    ("board", "Board"),
    ("white", "White"),
    ("black", "Black"),
    ("players", "White - Black"),
    ("move", "Move"),
    ("result", "Result"),
)

SAMPLE_VALUES = {"board": "6", "white": "Nowak Jan", "black": "Kowalski Piotr", "move": "34. Nf3",
                 "result": "1-0"}


@dataclass
class PushTypeSettings:
    enabled: bool = True
    priority: int = PRIORITY_NORMAL
    title_template: str = DEFAULT_TITLE
    body_template: str = DEFAULT_BODY


def _default_types() -> Dict[str, PushTypeSettings]:
    types = {}
    for code, (_, priority) in EVENT_TYPES.items():
        if code == "GAME_END":
            types[code] = PushTypeSettings(priority=priority, title_template=GAME_END_TITLE,
                                           body_template=GAME_END_BODY)
            continue
        if code == ERR_PGN:
            from src.i18n import tr
            types[code] = PushTypeSettings(priority=priority, title_template=ERR_PGN_TITLE,
                                           body_template=tr(ERR_PGN_BODY))
        else:
            types[code] = PushTypeSettings(priority=priority)
    return types


def clamp_priority(value) -> int:
    try:
        return max(PRIORITY_LOWEST, min(PRIORITY_HIGH, int(value)))
    except (TypeError, ValueError):
        return PRIORITY_NORMAL


@dataclass
class PushSettings:
    """Stored in options.json under the "push" key."""
    enabled: bool = False
    provider: str = "pushover"
    user_key: str = ""
    app_token: str = ""
    types: Dict[str, PushTypeSettings] = field(default_factory=_default_types)

    @property
    def configured(self) -> bool:
        return bool(self.user_key.strip() and self.app_token.strip())

    def to_dict(self) -> dict:
        data = asdict(self)
        data["types"] = {code: asdict(t) for code, t in self.types.items()}
        return data

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "PushSettings":
        data = data or {}
        settings = cls(
            enabled=bool(data.get("enabled", False)),
            provider=str(data.get("provider") or "pushover"),
            user_key=str(data.get("user_key") or ""),
            app_token=str(data.get("app_token") or ""),
        )
        stored = data.get("types") or {}
        for code, default in settings.types.items():
            item = stored.get(code)
            if not isinstance(item, dict):
                continue
            default.enabled = bool(item.get("enabled", default.enabled))
            default.priority = clamp_priority(item.get("priority", default.priority))
            default.title_template = str(item.get("title_template", default.title_template))
            default.body_template = str(item.get("body_template", default.body_template))
        return settings


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    priority: int = PRIORITY_NORMAL


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _render(template: str, values: dict) -> str:
    try:
        return template.format_map(_SafeDict(values))
    except (ValueError, IndexError, AttributeError):
        return template


def split_players(players: str) -> Tuple[str, str]:
    if " - " in players:
        white, black = players.split(" - ", 1)
        return white.strip(), black.strip()
    return players.strip(), ""


def build_message(settings: PushSettings, code: str, board: str = "",
                  white: str = "", black: str = "", move: str = "", result: str = "",
                  force: bool = False) -> Optional[PushMessage]:
    """Render the message for an event, or None when the type is switched off (unless forced)."""
    type_settings = settings.types.get(code)
    if type_settings is None or (not type_settings.enabled and not force):
        return None
    return render_message(code, type_settings, board, white, black, move, result)


def render_message(code: str, type_settings: PushTypeSettings, board: str = "",
                   white: str = "", black: str = "", move: str = "", result: str = "") -> PushMessage:
    from src.i18n import tr
    label = tr(EVENT_TYPES.get(code, (code, 0))[0])
    players = f"{white} - {black}" if black else white
    values = {
        "code": code, "type": label, "board": board or "",
        "white": white, "black": black, "players": players, "move": move or "",
        "result": result or "",
    }
    title = _render(type_settings.title_template, values).strip().rstrip("#").strip()
    body = _render(type_settings.body_template, values).strip()
    # A message without text is rejected by Pushover; fall back to something useful.
    if not body or body == "-":
        body = label
    return PushMessage(title=title or label, body=body, priority=type_settings.priority)


class PushProvider(ABC):
    """A delivery backend. ``send`` is blocking and runs on a worker thread."""

    name = "base"

    @abstractmethod
    def send(self, message: PushMessage) -> None:
        """Deliver the message or raise PushError."""
