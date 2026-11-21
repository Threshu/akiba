import logging
import urllib.request
from typing import Tuple
from urllib.error import HTTPError, URLError
import certifi

from src.helpers import get_ssl_context
import time

logger = logging.getLogger(__name__)

# Fixed header that mimics a regular browser
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "X-Client": "ChessClaimTool/0.4.3"
}

def fetch_pgn(url: str, timeout=30) -> Tuple[bytes, str]:
    """
    Download PGN from URL with up to 3 attempts.

    404 ('PGN not ready yet') and an empty body (live servers rewriting the
    file) are retried after a short pause.

    Returns:
        (data, "") on success, (b"", reason) on failure.
    """
    reason = ""
    for attempt in range(1, 4):  # 3 attempts
        try:
            req = urllib.request.Request(url, headers=BROWSER_HEADERS)

            response = urllib.request.urlopen(
                req,
                timeout=timeout,
                context=get_ssl_context(certifi.where())
            )
            data = response.read()
            if data:
                return data, ""
            reason = "empty file"
            time.sleep(0.5)
            continue

        except HTTPError as e:
            reason = f"HTTP {e.code}"
            if e.code == 404:
                time.sleep(3)
        except URLError as e:
            reason = f"network error: {e.reason}"
        except TimeoutError:
            reason = f"timeout after {timeout} s"
        except Exception as e:
            reason = f"{e.__class__.__name__}: {e}"
        logger.debug("download attempt %d failed for %s: %s", attempt, url, reason)

    time.sleep(1)

    return bytes(), reason


def download_pgn(url: str, timeout=30) -> bytes:
    """Download PGN from URL. Returns bytes() on failure (see fetch_pgn)."""
    return fetch_pgn(url, timeout)[0]


def check_download(url: str, timeout=10) -> bool:
    """
    Check if URL is reachable.
    Returns True if server responds, False otherwise.
    """

    try:
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)

        urllib.request.urlopen(
            req,
            timeout=timeout,
            context=get_ssl_context(certifi.where())
        )
        return True

    except Exception as e:
        return False


def check_livechess_download(url: str, timeout=10) -> bool:
    """
    Check if LiveChessCloud URL is valid by extracting tournament ID and fetching tournament data.
    Returns True if tournament exists, False otherwise.
    """
    from src.models.livechesscloud import extract_tournament_id, fetch_tournament
    
    tournament_id = extract_tournament_id(url)
    if not tournament_id:
        return False
    
    # Try to fetch tournament metadata
    tournament = fetch_tournament(tournament_id)
    return tournament is not None
