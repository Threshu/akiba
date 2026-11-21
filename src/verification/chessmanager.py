"""
Result verification against ChessManager (chessmanager.com).

ChessManager is the source of truth for results; the live PGN transmission can
be wrong (a board not updated, a wrong result entered on the DGT board). For
every round present in the PGN we read the round page of each configured
tournament and compare results game by game.

Access notes: chessmanager.com is behind Cloudflare. Plain scripted clients
(e.g. a bare "Mozilla/5.0" user agent) get HTTP 403; requests that look like a
normal browser are served. robots.txt allows general clients and asks for a
crawl delay of 3 s, which is respected between requests.
"""
from __future__ import annotations

import html
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.chessmanager.com"
CRAWL_DELAY = 3.0  # seconds, from chessmanager.com/robots.txt
TIMEOUT = 20

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}

# ChessManager result cell -> PGN result
RESULTS = {
    "1:0": "1-0", "0:1": "0-1", "½:½": "1/2-1/2", "1/2:1/2": "1/2-1/2", "0.5:0.5": "1/2-1/2",
    "+:-": "1-0", "-:+": "0-1", "-:-": "0-0", "0:0": "0-0",
}


class VerificationError(Exception):
    pass


def tournament_id(url: str) -> Optional[str]:
    """Tournament id from any ChessManager link (also tolerates typos in the host)."""
    match = re.search(r"tournaments/(\d+)", url or "")
    return match.group(1) if match else None


def normalize_name(name: str) -> str:
    """'Kicińska, Anna (2051)' and 'Kicinska, Anna' -> 'kicinska anna'."""
    name = re.sub(r"\(.*?\)", " ", name or "")
    name = name.replace("ł", "l").replace("Ł", "L")
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^a-zA-Z]+", " ", name).strip().lower()
    return name


@dataclass
class Pairing:
    tournament: str
    round: str
    board: str
    white: str
    black: str
    result: str  # PGN notation, "" when not entered yet


@dataclass
class Discrepancy:
    game_index: int
    board: str
    round: str
    white: str
    black: str
    pgn_result: str
    cm_result: str
    note: str = ""


@dataclass
class VerificationReport:
    checked: int = 0
    discrepancies: List[Discrepancy] = field(default_factory=list)
    not_found: List[Tuple[int, str, str, str]] = field(default_factory=list)  # (game_index, round, white, black)
    pending_in_cm: int = 0      # finished in PGN, no result in ChessManager yet
    errors: List[str] = field(default_factory=list)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_round(page: str, tournament: str, rnd: str) -> List[Pairing]:
    """Pairings with results from a ChessManager round page."""
    start, end = page.find("<tbody"), page.find("</tbody>")
    if start < 0 or end < 0:
        return []
    pairings = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page[start:end], re.S):
        players = [_text(a) for a in re.findall(r'<a[^>]*/players/[^>]*>(.*?)</a>', row, re.S)]
        cells = [_text(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(players) < 2 or not cells:
            continue  # bye / empty row
        board = cells[0].rstrip(".")
        result = ""
        for cell in cells:
            key = cell.replace(" ", "")
            if ":" in key and key in RESULTS:
                result = RESULTS[key]
                break
        pairings.append(Pairing(tournament, rnd, board, players[0], players[1], result))
    return pairings


def fetch_round(session: requests.Session, tournament: str, rnd: str) -> List[Pairing]:
    url = f"{BASE_URL}/pl-pl/tournaments/{tournament}/rounds/{rnd}"
    try:
        response = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise VerificationError(f"{url}: {exc.__class__.__name__}") from exc
    if response.status_code == 404:
        return []  # round does not exist in this tournament
    if response.status_code != 200:
        raise VerificationError(f"{url}: HTTP {response.status_code}")
    return parse_round(response.text, tournament, rnd)


def round_of(game) -> str:
    rnd = str(game.headers.get("Round", "")).strip()
    if not rnd or rnd in ("?", "-"):
        return ""
    return rnd.split(".")[0]


def verify(games: List, urls: Iterable[str],
           progress: Optional[Callable[[str], None]] = None) -> VerificationReport:
    """Compare results of the PGN games with ChessManager. Blocking (network)."""
    report = VerificationReport()
    tournaments = []
    for url in urls:
        tid = tournament_id(url)
        if tid and tid not in tournaments:
            tournaments.append(tid)
    if not tournaments:
        report.errors.append("no ChessManager tournaments configured")
        return report

    rounds = sorted({round_of(g) for g in games if round_of(g)}, key=lambda r: (len(r), r))
    pairings: Dict[Tuple[str, str], Tuple[Pairing, bool]] = {}  # (round, players key) -> (pairing, swapped)
    session = requests.Session()
    first = True
    for tid in tournaments:
        for rnd in rounds:
            if not first:
                time.sleep(CRAWL_DELAY)
            first = False
            if progress:
                progress(f"{tid} / {rnd}")
            try:
                for p in fetch_round(session, tid, rnd):
                    w, b = normalize_name(p.white), normalize_name(p.black)
                    pairings[(rnd, w, b)] = (p, False)
                    pairings.setdefault((rnd, b, w), (p, True))
            except VerificationError as exc:
                logger.warning("ChessManager: %s", exc)
                report.errors.append(str(exc))

    for index, game in enumerate(games):
        rnd = round_of(game)
        white = game.headers.get("White", "")
        black = game.headers.get("Black", "")
        key = (rnd, normalize_name(white), normalize_name(black))
        found = pairings.get(key)
        if found is None:
            report.not_found.append((index, rnd, white, black))
            continue
        pairing, swapped = found
        report.checked += 1
        pgn_result = game.headers.get("Result", "*")
        if not pairing.result:
            if pgn_result != "*":
                report.pending_in_cm += 1
            continue
        if swapped:
            report.discrepancies.append(Discrepancy(
                index, pairing.board, rnd, white, black, pgn_result, pairing.result,
                "colours swapped in transmission"))
            continue
        if pgn_result != pairing.result:
            report.discrepancies.append(Discrepancy(
                index, pairing.board, rnd, white, black, pgn_result, pairing.result))
    logger.info("ChessManager verification: %d checked, %d discrepancies, %d not found",
                report.checked, len(report.discrepancies), len(report.not_found))
    return report
