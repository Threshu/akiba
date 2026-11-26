"""
Results from DGT boards are read from the king placement after the game, so
while the players are setting the kings the board may briefly report another
result (kings facing each other = draw on the way to "both kings on white").
A changed result is passed on only once it has stayed the same for
SETTLE_SECONDS; until then the previous result of the game is kept.
"""

import logging
import re
import time

from src.helpers import stable_game_id

logger = logging.getLogger(__name__)

SETTLE_SECONDS = 60

_HEADER_BLOCK = re.compile(r'(?:^\[[A-Za-z0-9_]+[ \t]+"(?:[^"\\\n]|\\.)*"\][ \t]*\r?\n?)+', re.M)
_TAG = re.compile(r'^\[([A-Za-z0-9_]+)[ \t]+"((?:[^"\\\n]|\\.)*)"\]', re.M)
_RESULT_TAG = re.compile(r'^(\[Result[ \t]+")[^"\n]*("\])', re.M)
_END_TOKEN = re.compile(r'(1-0|0-1|1/2-1/2|\*)(\s*)$')


class ResultSettler:
    """Keeps transient board results out of the combined PGN."""

    def __init__(self, settle_seconds: float = SETTLE_SECONDS, clock=time.monotonic):
        self.settle_seconds = settle_seconds
        self.clock = clock
        self._settled = {}   # game key -> result passed on
        self._pending = {}   # game key -> (new result, first seen)

    def filter(self, data: bytes) -> bytes:
        text = data.decode("utf-8", "surrogateescape")
        blocks = list(_HEADER_BLOCK.finditer(text))
        if not blocks:
            return data
        now = self.clock()
        out = [text[:blocks[0].start()]]
        seen = {}
        for i, block in enumerate(blocks):
            end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
            header, body = block.group(0), text[block.end():end]
            tags = dict(_TAG.findall(header))
            key = stable_game_id(tags)
            seen[key] = seen.get(key, 0) + 1
            key = f"{key}#{seen[key]}"   # same players twice in one file
            result = tags.get("Result", "*")
            shown = self._shown_result(key, result, now)
            if shown != result:
                header = _RESULT_TAG.sub(lambda m: m.group(1) + shown + m.group(2), header)
                body = _END_TOKEN.sub(lambda m: shown + m.group(2), body)
            out.append(header + body)
        return "".join(out).encode("utf-8", "surrogateescape")

    def _shown_result(self, key: str, result: str, now: float) -> str:
        settled = self._settled.get(key)
        if settled is None or settled == result:
            # first sighting (the game is taken as it is) or nothing changed
            if self._pending.pop(key, None) is not None:
                logger.info("result back to %s, change dropped: %s", result, key)
            self._settled[key] = result
            return result
        pending = self._pending.get(key)
        if pending is None or pending[0] != result:
            self._pending[key] = (result, now)
            logger.info("result %s -> %s held back: %s", settled, result, key)
            return settled
        if now - pending[1] >= self.settle_seconds:
            logger.info("result %s -> %s accepted after %.0f s: %s",
                        settled, result, now - pending[1], key)
            self._settled[key] = result
            del self._pending[key]
            return result
        return settled
