"""Defaults for main.py."""
from pathlib import Path

DB_PATH = Path("rrk.db")
AD_TYPE = "12"  # P199_NOE_AGAHI
PAGE_SIZE = 100  # what the browser asks for
TIMEOUT = 180.0  # seconds per response: one grid page takes 30-40 s on the server
WORKERS = 12  # detail-page workers per session: ~40 pages/min each keeps up with its listing (~480 rows/min)
DETAIL_BACKLOG = 2000  # links a session lets wait for its workers before listing on
DETAIL_DELAY = 0.0  # seconds each worker waits between detail pages
PAGE_DELAY = 2.0  # seconds a session waits between two grid pages of one window
MAX_ATTEMPTS = 3  # detail pages that failed this often are skipped until looked at
TYPE_ATTEMPTS = 3  # fresh sessions per ad type when the network or the session fails
SESSION_RETRY_WAIT = 60.0  # seconds before such a fresh session
TYPES_AT_ONCE = 1  # ad types scraped at the same time, each in its own session
# 2026-10-30 runs: ~70 detail requests at once was fastest (2,300-3,100 pages/min); 400+ made the site drop
# connections and got fewer pages.  Keep TYPES_AT_ONCE * SESSIONS_PER_TYPE * WORKERS near 60.
SESSIONS_PER_TYPE = 5  # sessions sharing one type's date windows, each with its own workers
