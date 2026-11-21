"""
Decide whether an illegal move in a live PGN is a real (completed) move.

DGT boards publish a move as soon as the board sees it; the clock time
comment {[%clk ...]} is added only when the player presses the clock. A
piece put down on a wrong square for a moment therefore shows up as an
"illegal move" that disappears again once it is corrected. Only a move that
was followed by a clock press (its own %clk comment) or by another move is
treated as completed.
"""
from __future__ import annotations

import re
from typing import Tuple

_TOKEN = re.compile(
    r"\{(?P<comment>[^}]*)\}"          # {comment}
    r"|;[^\n]*"                         # ; line comment
    r"|\$\d+"                           # NAG
    r"|\d+\.(?:\.\.)?"                  # move number 12. / 12...
    r"|(?P<result>1-0|0-1|1/2-1/2|\*)"  # result
    r"|[()]"                            # variations
    r"|(?P<san>[A-Za-z][A-Za-z0-9+#=\-]*)"
)


def _movetext(raw_game: str) -> str:
    # Headers are lines starting with '['; the rest is the movetext
    return "\n".join(line for line in raw_game.splitlines() if not line.lstrip().startswith("["))


def illegal_move_status(raw_game: str, valid_moves: int) -> Tuple[bool, bool]:
    """
    Args:
        raw_game: PGN text of one game (headers + movetext).
        valid_moves: number of moves python-chess could play before the illegal one.

    Returns:
        (completed, has_clock):
            completed - the illegal move got its own clock time or another move followed;
            has_clock - the game carries clock times at all.
    """
    text = _movetext(raw_game)
    has_clock = "%clk" in text
    san_index = -1
    illegal_seen = False
    for match in _TOKEN.finditer(text):
        if match.group("san"):
            san_index += 1
            if san_index == valid_moves:
                illegal_seen = True
            elif illegal_seen:
                return True, has_clock          # another move after the illegal one
        elif match.group("comment") is not None and illegal_seen:
            if "%clk" in match.group("comment"):
                return True, has_clock          # clock pressed after the illegal move
        elif match.group("result"):
            break
    return False, has_clock
