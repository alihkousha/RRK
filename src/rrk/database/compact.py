"""Shrink the scraper's database: pack the plain texts (database.texts), drop the listing
summaries of ads whose text is stored, then rewrite the file without the freed pages.
Run with the GUI and every scrape stopped:  python -m database.compact [--db rrk.db]
Safe to run again: it trains a dictionary only once and packs only what is still plain.
The rewrite needs free disk about the size of the result."""
import argparse
import logging
import os
import sqlite3
import time
import zlib
from pathlib import Path

import config
from database import texts
from database.connection import connect

log = logging.getLogger("rrk.compact")
BATCH = 20_000  # rowids per transaction: keeps the WAL small
SAMPLE = 20_000  # texts the dictionary is trained on


def _batches(conn: sqlite3.Connection, table: str):
    top = conn.execute(f"SELECT coalesce(max(rowid), 0) FROM {table}").fetchone()[0]
    for low in range(0, top + 1, BATCH):
        yield low, low + BATCH
        if low // BATCH % 25 == 0:
            log.info("%s: rowid %d of %d", table, low, top)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def compact(db: Path) -> None:
    conn = connect(db)
    dicts = texts.load_dicts(conn)
    if not dicts:
        top = conn.execute("SELECT coalesce(max(rowid), 0) FROM details").fetchone()[0]
        step = max(1, top // SAMPLE)
        sample = [r[0] for r in conn.execute(
            "SELECT full_text FROM details WHERE rowid % ? = 0 AND typeof(full_text) = 'text'", (step,))]
        with conn:
            conn.execute("INSERT INTO text_dicts (id, dict) VALUES (1, ?)", (texts.train(sample),))
        dicts = texts.load_dicts(conn)
        log.info("trained a dictionary on %d texts", len(sample))
    dict_id, zdict = max(dicts.items())
    def pack(text):  # packed in place: each text is checked to come back whole first
        packed = texts.pack(text, dict_id, zdict)
        unpacker = zlib.decompressobj(zdict=zdict)
        if (unpacker.decompress(packed[1:]) + unpacker.flush()).decode() != text:
            raise ValueError("a packed text doesn't unpack to itself")
        return packed

    conn.create_function("pack", 1, pack)
    for low, high in _batches(conn, "details"):
        with conn:
            conn.execute("UPDATE details SET full_text = pack(full_text)"
                         " WHERE rowid >= ? AND rowid < ? AND typeof(full_text) = 'text'", (low, high))
    for low, high in _batches(conn, "ads"):
        with conn:
            conn.execute("UPDATE ads SET summary = NULL WHERE rowid >= ? AND rowid < ? AND summary IS NOT NULL"
                         " AND EXISTS (SELECT 1 FROM details WHERE details.detail_code = ads.detail_code"
                         " AND full_text IS NOT NULL)",  # a key lookup per row: IN (...) scanned every text per batch
                         (low, high))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    part = db.with_name(db.name + ".compact")
    part.unlink(missing_ok=True)
    started = time.monotonic()
    log.info("rewriting %s without its free pages", db)
    conn.execute("VACUUM INTO ?", (str(part),))
    conn.close()
    wal = db.with_name(db.name + "-wal")
    if wal.exists() and wal.stat().st_size:  # another process has it open: its WAL would corrupt the new file
        raise SystemExit(f"{db} is still open elsewhere; stop the GUI and scrapes, then run this again ({part} kept)")
    before = db.stat().st_size
    os.replace(part, db)  # the old WAL was checkpointed and is gone with its last connection
    log.info("%s: %.1f GB -> %.1f GB in %.0f s", db, before / 1e9, db.stat().st_size / 1e9, time.monotonic() - started)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    compact(parser.parse_args().db)
