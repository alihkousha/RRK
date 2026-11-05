from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any

from database import texts
from database.models import DAY, NOTICE
from models.advertisement import AdDetail, Advertisement

log = logging.getLogger("rrk.db")

_FIELDS = [f.name for f in fields(Advertisement)]
_COLUMNS = [*_FIELDS, "ad_type", "source", "first_seen", "last_seen"]
_DETAIL_FIELDS = [f.name for f in fields(AdDetail)]
_UPSERT = (
    f"INSERT INTO ads ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})"
    " ON CONFLICT(detail_code) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c not in ("detail_code", "first_seen", "summary"))
    # The listing's summary is the text's first 200 characters: not kept once the text is stored.
    + ", summary = CASE WHEN EXISTS (SELECT 1 FROM details WHERE details.detail_code = excluded.detail_code"
    " AND full_text IS NOT NULL) THEN NULL ELSE excluded.summary END"
)


class AdRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        dicts = texts.load_dicts(conn)  # the newest packs new texts; none yet: stored plain
        self.text_dict = max(dicts.items()) if dicts else None

    def upsert_ads(self, ads: Iterable[Advertisement], *, ad_type: str, seen_at: str,
                   source: str | None = None) -> int:
        """Insert new ads and refresh known ones in one transaction; returns
        how many were stored.  ``first_seen`` keeps its first value."""
        ads = list(ads)
        rows = [(*(getattr(ad, f) for f in _FIELDS), ad_type, source, seen_at, seen_at)
                for ad in ads if ad.detail_code]
        if len(rows) < len(ads):  # none so far: all 554 type-12 rows have one
            log.warning("skipped %d rows without a detail_code", len(ads) - len(rows))
        with self.conn:
            self.conn.executemany(_UPSERT, rows)
        return len(rows)

    def save_ad_types(self, types: Iterable[tuple[str, str]], seen_at: str) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO ad_types (code, label, first_seen, last_seen) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(code) DO UPDATE SET label = excluded.label, last_seen = excluded.last_seen",
                [(code, label, seen_at, seen_at) for code, label in types])

    def all_ads(self) -> Iterator[dict[str, Any]]:
        """Every ad with its detail fields, one at a time (millions of them don't fit in memory)."""
        return (dict(row) for row in self.conn.execute(
            "SELECT ads.*, "
            + ", ".join(f"unzip(details.{f}) AS {f}" if f == "full_text" else "details." + f for f in _DETAIL_FIELDS)
            + " FROM ads LEFT JOIN details USING (detail_code) ORDER BY ads.rowid"))

    def export(self, path: Path) -> int:
        """Write :meth:`all_ads` to *path* as one gzipped JSON list (``json.dumps(ads, indent=1)``),
        an ad at a time, replacing the file only once it's complete.  Returns the ads written."""
        part = path.with_name(path.name + ".part")
        count = 0
        with gzip.open(part, "wt", encoding="utf-8") as out:
            out.write("[")
            for count, ad in enumerate(self.all_ads(), 1):
                out.write(",\n " if count > 1 else "\n ")
                out.write(json.dumps(ad, ensure_ascii=False, indent=1).replace("\n", "\n "))
            out.write("\n]" if count else "]")
        part.replace(path)
        return count

    def windows_left(self, ad_type: str) -> list[str]:
        """The windows (as JSON) a stopped run of *ad_type* hadn't finished, in the order to list them."""
        return [row[0] for row in self.conn.execute(
            "SELECT spec FROM windows_left WHERE ad_type = ? ORDER BY rowid", (ad_type,))]

    def save_windows_left(self, ad_type: str, specs: Iterable[str]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM windows_left WHERE ad_type = ?", (ad_type,))
            self.conn.executemany("INSERT INTO windows_left (ad_type, spec) VALUES (?, ?)",
                                  [(ad_type, spec) for spec in specs])

    def codes_without_detail(self, max_attempts: int) -> list[str]:
        """Ads whose detail page isn't stored yet and hasn't failed *max_attempts* times."""
        return [row[0] for row in self.conn.execute(
            "SELECT detail_code FROM ads LEFT JOIN details USING (detail_code)"
            " WHERE fetched_at IS NULL AND coalesce(attempts, 0) < ? ORDER BY ads.rowid",
            (max_attempts,))]

    def detail_gaps(self, ad_type: str, max_attempts: int) -> list[tuple[str, str | None, int, int]]:
        """(letter date, newspaper date, ads, ads still without a detail page)
        of *ad_type*, for :func:`scraper.windows.gap_windows`."""
        return [tuple(row) for row in self.conn.execute(
            "SELECT letter_date, newspaper_date, count(*),"
            " sum(fetched_at IS NULL AND coalesce(attempts, 0) < ?)"
            " FROM ads LEFT JOIN details USING (detail_code)"
            " WHERE ad_type = ? AND letter_date IS NOT NULL GROUP BY 1, 2",  # never NULL so far
            (max_attempts, ad_type))]

    def newest_newspaper_date(self, ad_type: str, until: str) -> str | None:
        """The newest newspaper date of *ad_type*'s ads up to *until*: a typo past it would hide every
        notice printed before it from a --new-only run."""
        return self.conn.execute("SELECT max(newspaper_date) FROM ads WHERE ad_type = ? AND newspaper_date <= ?",
                                 (ad_type, until)).fetchone()[0]

    def finished_detail_codes(self, max_attempts: int, codes: Iterable[str]) -> set[str]:
        """Which of *codes* have their detail page stored or failed *max_attempts* times."""
        codes, found = list(codes), set()
        for start in range(0, len(codes), 500):
            part = codes[start:start + 500]
            found.update(row[0] for row in self.conn.execute(
                f"SELECT detail_code FROM details WHERE detail_code IN ({','.join('?' * len(part))})"
                " AND (fetched_at IS NOT NULL OR attempts >= ?)", (*part, max_attempts)))
        return found

    def given_up_details(self, max_attempts: int) -> list[tuple[str, int, str | None]]:
        """Detail pages no run fetches any more: (code, attempts, last error)."""
        return [tuple(row) for row in self.conn.execute(
            "SELECT detail_code, attempts, last_error FROM details"
            " WHERE fetched_at IS NULL AND attempts >= ? ORDER BY detail_code", (max_attempts,))]

    def save_detail(self, code: str, detail: AdDetail, fetched_at: str) -> None:
        columns = [*_DETAIL_FIELDS, "fetched_at"]
        with self.conn:
            self.conn.execute(
                f"INSERT INTO details (detail_code, {', '.join(columns)}, attempts)"
                f" VALUES (?, {', '.join('?' * len(columns))}, 1) ON CONFLICT(detail_code) DO UPDATE SET "
                + ", ".join(f"{c} = excluded.{c}" for c in columns)
                + ", attempts = attempts + 1, last_error = NULL",
                (code, *(self._pack(detail.full_text) if f == "full_text" else getattr(detail, f)
                         for f in _DETAIL_FIELDS), fetched_at))
            if detail.full_text is not None:
                self.conn.execute("UPDATE ads SET summary = NULL WHERE detail_code = ?", (code,))

    def _pack(self, text: str | None) -> bytes | str | None:
        return texts.pack(text, *self.text_dict) if self.text_dict else text

    def unread_texts(self, version: int, limit: int, after: int = 0) -> list[tuple]:
        """(details rowid, code, full text, company national id, company name, the version
        it was read with or None, NOTICE, DAY, issuer, ad type) of up to *limit* stored detail pages
        past rowid *after* that the explorer hasn't read with *version*."""
        return [tuple(row) for row in self.conn.execute(
            "SELECT details.rowid, detail_code, unzip(full_text), company_national_id, company_name, version,"
            f" {NOTICE}, {DAY}, details.issuer, ads.ad_type FROM details"
            " JOIN ads USING (detail_code) LEFT JOIN indexed USING (detail_code)"
            " WHERE details.rowid > ? AND fetched_at IS NOT NULL AND coalesce(version, -1) != ?"
            " ORDER BY details.rowid LIMIT ?", (after, version, limit))]

    def save_mentions(self, ads: list[tuple], version: int) -> None:
        """Replace what the explorer read from each ad: (details rowid, code, searchable
        text for ``ad_text`` or None when it is there already (texts never change),
        ``mentions`` rows in column order after detail_code, notice, day, subject, issuer,
        events, capital from, capital to, office)."""
        with self.conn:
            self.conn.executemany("DELETE FROM mentions WHERE detail_code = ?",
                                  [(code,) for _, code, text, *_ in ads if text is None])
            self.conn.executemany("INSERT INTO ad_text (rowid, text) VALUES (?, ?)",
                                  [(rowid, text) for rowid, _, text, *_ in ads if text is not None])
            self.conn.executemany(
                "INSERT INTO mentions (detail_code, kind, entity_id, written, valid, name, role, represents,"
                " subject, text_start, text_end, stake) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(code, *row) for _, code, _, rows, *_ in ads for row in rows])
            self.conn.executemany(
                "INSERT OR REPLACE INTO indexed (detail_code, version, notice, day, subject, issuer, events,"
                " capital_from, capital_to, office, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(code, version, *ad, rowid) for rowid, code, _, _, *ad in ads])

    def record_failure(self, code: str, error: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO details (detail_code, attempts, last_error) VALUES (?, 1, ?)"
                " ON CONFLICT(detail_code) DO UPDATE SET"
                " attempts = attempts + 1, last_error = excluded.last_error",
                (code, error))
