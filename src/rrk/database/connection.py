import sqlite3
from pathlib import Path

from . import texts
from .models import EXPLORE_SCHEMA, SCHEMA

# SQLite keeps a WAL file as big as its biggest write (a graph rebuild: >1 GB) unless told to shrink it.
WAL_LIMIT = 64 * 1024 * 1024


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database, creating the file and its tables if needed."""
    conn = sqlite3.connect(path, timeout=30)  # ad types running at once share the file
    conn.row_factory = sqlite3.Row
    # Reading (an export) doesn't block a running scrape.  The mode is kept in
    # the file; switching needs an exclusive lock, so only when it isn't set.
    if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:  # another connection is switching it now
            if "locked" not in str(exc):
                raise
    conn.execute(f"PRAGMA journal_size_limit = {WAL_LIMIT}")
    conn.executescript(SCHEMA)
    texts.register(conn, path)
    return conn


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open the scraper's database so that SQLite refuses any write."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    texts.register(conn, path)
    return conn


def explore_path(path: Path) -> Path:
    """The explorer's database beside the scraper's: rrk.db -> rrk.explore.db."""
    return path.with_name(f"{path.stem}.explore.db")


def connect_explorer(path: Path) -> sqlite3.Connection:
    """The explorer's database, creating it if needed, with the scraper's at *path*
    attached read-only: unqualified names find ``ads``, ``details`` and ``ad_types``
    there, as the explorer's own file never holds them."""
    conn = sqlite3.connect(explore_path(path).resolve().as_uri(), uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":  # the GUI reads while it indexes
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA journal_size_limit = {WAL_LIMIT}")
    conn.execute("ATTACH DATABASE ? AS src", (f"{path.resolve().as_uri()}?mode=ro",))
    conn.executescript(EXPLORE_SCHEMA)
    texts.register(conn, path, "src")
    # Older explorer databases: indexed's columns are filled by the next VERSION's reading, the graph's by the next build.
    for table, columns in (("indexed", ("issuer TEXT", "text INTEGER", "events TEXT", "capital_from INTEGER",
                                        "capital_to INTEGER", "office TEXT")),
                           ("mentions", ("stake INTEGER",)),
                           ("notices", ("events TEXT", "capital_from INTEGER", "capital_to INTEGER", "office TEXT", "roles TEXT",
                                        "subject TEXT")),
                           ("g_nodes", ("current INTEGER NOT NULL DEFAULT 0", "first TEXT", "last TEXT", "roles TEXT")),
                           ("g_edges", ("kind TEXT",))):
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column.split()[0] not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                if table != "indexed":
                    conn.execute("UPDATE g_meta SET mentions = -1")  # graph_outdated: rebuilt on the next round
    conn.commit()
    return conn
