"""
Followed players: notify the arbiter about selected events in their games
(by default only when a game of a followed player finishes).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

from src.verification.chessmanager import normalize_name

logger = logging.getLogger(__name__)

GAME_END = "GAME_END"


@dataclass
class FollowSettings:
    players: List[str] = field(default_factory=list)   # names as in the PGN
    events: Set[str] = field(default_factory=lambda: {GAME_END})

    def to_dict(self) -> dict:
        return {"players": list(self.players), "events": sorted(self.events)}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "FollowSettings":
        data = data or {}
        events = data.get("events")
        return cls(players=[str(p) for p in data.get("players", []) if str(p).strip()],
                   events=set(events) if isinstance(events, list) else {GAME_END})

    def _keys(self) -> Set[str]:
        return {normalize_name(p) for p in self.players}

    def is_followed(self, name: str) -> bool:
        return bool(name) and normalize_name(name) in self._keys()

    def followed_in(self, white: str, black: str) -> bool:
        return self.is_followed(white) or self.is_followed(black)


class GameEndTracker:
    """Detects games that got a result since the previous scan."""

    def __init__(self) -> None:
        self._known: Set[tuple] = set()
        self._primed = False
        self._ongoing: Set[str] = set()  # ids seen without a result

    def new_results(self, games: Iterable, make_id) -> List:
        """Games whose (id, result) was not seen before. The first call only primes;
        games that first appear already finished are not reported."""
        fresh = []
        for game in games:
            result = game.headers.get("Result", "*")
            game_id = make_id(game)
            if result == "*":
                self._ongoing.add(game_id)
                continue
            key = (game_id, result)
            if key in self._known:
                continue
            self._known.add(key)
            if self._primed and game_id in self._ongoing:
                fresh.append(game)
        self._primed = True
        return fresh
