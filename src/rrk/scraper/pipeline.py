from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, as_completed, wait
from contextlib import nullcontext
from datetime import datetime

import requests

from database.repository import AdRepository
from models.advertisement import AdDetail
from parsing import (ListingParseError, detail_links, fetched_data, parse_detail_page,
                     parse_listing_payload)
from rrs.client import RRSClient

from .windows import CAP, Window

log = logging.getLogger("rrk.pipeline")


def scrape_listing(client: RRSClient, repo: AdRepository, *, ad_type: str, page_size: int,
                   on_links: Callable[[dict[str, str]], None] | None = None) -> dict[str, str]:
    """Store every grid page of *ad_type* as it arrives, one commit per page,
    so a failure keeps the pages before it.  A query that hits the server's
    999-row cap is split into date windows (see :mod:`scraper.windows`), all
    in this one session.  Returns this session's detail links
    (``detail_code -> link``), also passed to *on_links* page by page."""
    seen_at = datetime.now().isoformat(timespec="seconds")
    links: dict[str, str] = {}
    windows = [Window()]
    while windows:
        windows.extend(reversed(list_window(client, repo, windows.pop(), ad_type=ad_type,
                                            page_size=page_size, seen_at=seen_at,
                                            links=links, on_links=on_links)))
    return links


def list_window(client: RRSClient, repo: AdRepository, window: Window, *, ad_type: str,
                page_size: int, seen_at: str, links: dict[str, str],
                on_links: Callable[[dict[str, str]], None] | None = None,
                on_capped: Callable[[Window], None] | None = None,
                choices: Mapping[str, Sequence[str]] | None = None, page_delay: float = 2.0) -> list[Window]:
    """Store the grid pages of one window, adding their detail links to
    *links* (and passing them to *on_links*).  If the window hits the cap,
    its first page is kept and the smaller windows that cover the rest are
    returned; otherwise ``[]``.  A capped window that can't be split keeps
    its 999 rows and goes to *on_capped*.  *choices*: see :meth:`Window.split`."""
    archive = client.session.archive
    pages = client.iter_pages(noe_agahi=ad_type, page_size=page_size, delay=page_delay,
                              filters=window.filters() or None)
    for number, response in enumerate(pages):
        data = fetched_data(response)
        repo.upsert_ads(parse_listing_payload(response), ad_type=ad_type, seen_at=seen_at,
                        source=archive.last_file.name if archive else None)
        page_links = detail_links(response)
        links.update(page_links)
        if on_links:
            on_links(page_links)
        log.info("type %s (%s): stored rows %d-%d of %s", ad_type, window, data["firstRow"],
                 data["firstRow"] + len(data["values"]) - 1, data.get("totalRows", "?"))
        if number == 0 and data.get("totalRows", 0) >= CAP:
            parts = window.split(choices)
            if parts:
                log.info("type %s: %s hits the %d-row cap; splitting it in %d",
                         ad_type, window, CAP, len(parts))
                pages.close()
                return parts
            log.warning("type %s: %s hits the %d-row cap and can't be split further:"
                        " keeping its first %d rows", ad_type, window, CAP, CAP)
            if on_capped:
                on_capped(window)
    return []


class WindowQueue:
    """The date windows of one ad type, shared by its sessions.  A session
    that finds the queue empty waits while another may still split a window.
    *save* gets the unfinished windows (:meth:`pending`) after every change:
    a stop, even a kill, then loses no window.  A window still capped after
    every split stays unfinished too (:meth:`capped`): a later run lists it
    again, and splits it if the splitter by then can."""

    def __init__(self, windows: Sequence[Window],
                 save: Callable[[list[Window]], None] | None = None) -> None:
        self._todo = list(reversed(windows))
        self._taken: list[Window] = []
        self._capped: list[Window] = []
        self._aborted = False
        self._changed = threading.Condition()
        self._save = save
        self._saving = threading.Lock()  # saves in the order of the changes

    def take(self) -> Window | None:
        """The next window, or None once nothing is left (or after :meth:`abort`)."""
        with self._changed:
            while not self._todo and self._taken and not self._aborted:
                self._changed.wait()
            if self._aborted or not self._todo:
                return None
            window = self._todo.pop()
            self._taken.append(window)
        self._saved()
        return window

    def done(self, window: Window, parts: list[Window] = (), *, failed: bool = False) -> None:
        """A taken *window* is finished: queue its *parts*, or put it back if it *failed*."""
        with self._changed:
            self._taken.remove(window)
            self._todo.extend([window] if failed else reversed(parts))
            self._changed.notify_all()
        self._saved()

    def capped(self, window: Window) -> None:
        """A taken *window* hits the cap and can't be split: not taken again, but left unfinished."""
        with self._changed:
            self._capped.append(window)

    def abort(self) -> None:
        with self._changed:
            self._aborted = True
            self._changed.notify_all()

    def pending(self) -> list[Window]:
        """The windows not finished: those being listed, the rest in the order they'd be taken, then
        the capped ones."""
        with self._changed:
            return self._taken + self._todo[::-1] + [w for w in self._capped if w not in self._taken]

    def _saved(self) -> None:
        if self._save:
            with self._saving:
                self._save(self.pending())


def scrape_type(new_client: Callable[[int, int], RRSClient], new_repo: Callable[[], AdRepository],
                ad_type: str, *, sessions: int, attempts: int, retry_wait: float, page_size: int,
                workers: int, delay: float, max_attempts: int,
                detail_limit: int | None, backlog: int | None = None,
                windows: Sequence[Window] = (Window(),),
                save_left: Callable[[list[Window]], None] | None = None,
                choices: Mapping[str, Sequence[str]] | None = None, page_delay: float = 2.0) -> tuple[int, int]:
    """Listing and the missing detail pages of one ad type, in *sessions*
    sessions that share its date windows (:class:`WindowQueue`).  Each session
    has its own database connection and its own *workers* detail workers,
    which start on each grid page's links as it is stored: links only work
    in the session that listed them.  A session opens its search only when it
    gets its first window, so a type under the cap uses one session.  It
    takes a new window only once at most *backlog* links wait for a worker:
    links not fetched when a run stops cost a re-listing (*windows*: by
    default the whole listing; :func:`scraper.windows.gap_windows` for only
    the dates with missing details).  *save_left* gets the windows not
    finished yet whenever they change (see :class:`WindowQueue`).  *choices*:
    the dropdown codes a window capped on one day of both dates is split by.
    Returns (ads listed, detail pages stored).

    A session whose network fails beyond the HTTP retries, or whose session
    ended, puts its window back and continues in a fresh session from
    ``new_client(session, attempt)``, up to *attempts* times; details it had
    not fetched yet wait for the next run.  A wrong detail page (firewall,
    APEX error) stops every session of the type at once."""
    queue = WindowQueue(windows, save_left)
    seen_at = datetime.now().isoformat(timespec="seconds")

    def run_session(number: int) -> tuple[int, int]:
        repo = new_repo()
        listed = stored = 0
        for attempt in range(1, attempts + 1):
            try:
                with new_client(number, attempt) as client, \
                        (DetailFetcher(client, repo, workers=workers, delay=delay,
                                       max_attempts=max_attempts, limit=detail_limit)
                         if detail_limit != 0 else nullcontext()) as details:
                    links: dict[str, str] = {}
                    while True:
                        if details and backlog is not None:
                            details.wait_below(backlog)
                        if (window := queue.take()) is None:
                            break
                        try:
                            parts = list_window(client, repo, window, ad_type=ad_type,
                                                page_size=page_size, seen_at=seen_at, links=links,
                                                on_links=details.add if details else None,
                                                on_capped=queue.capped, choices=choices,
                                                page_delay=page_delay)
                        except BaseException:
                            queue.done(window, failed=True)
                            raise
                        queue.done(window, parts)
                    listed += len(links)
                    stored += details.finish() if details else 0
                    return listed, stored
            except (requests.RequestException, ListingParseError) as exc:
                if attempt == attempts:
                    raise
                log.warning("type %s session %d: %s: %s; fresh session in %.0fs (attempt %d of %d)",
                            ad_type, number, type(exc).__name__, exc, retry_wait, attempt + 1, attempts)
                time.sleep(retry_wait)
            except BaseException:
                queue.abort()  # a wrong page may be the firewall: stop every session
                raise
        raise AssertionError("unreachable")

    listed = stored = 0
    errors: list[BaseException] = []
    with ThreadPoolExecutor(sessions, thread_name_prefix=f"type{ad_type}") as pool:
        for future in as_completed([pool.submit(run_session, n) for n in range(1, sessions + 1)]):
            try:
                session_listed, session_stored = future.result()
                listed, stored = listed + session_listed, stored + session_stored
            except BaseException as exc:
                errors.append(exc)
    if errors:
        log.error("type %s: %d session(s) failed, %d window(s) left for the next run",
                  ad_type, len(errors), len(queue.pending()))
        raise errors[0]
    if capped := queue.pending():
        log.warning("type %s: %d window(s) still capped after every split, kept among the windows left",
                    ad_type, len(capped))
    return listed, stored


def fetch_details(client: RRSClient, repo: AdRepository, links: dict[str, str], *,
                  workers: int, delay: float, max_attempts: int,
                  limit: int | None = None) -> int:
    """Fetch and store the detail pages of *links* still missing; see
    :class:`DetailFetcher`.  Returns the pages stored."""
    with DetailFetcher(client, repo, workers=workers, delay=delay,
                       max_attempts=max_attempts, limit=limit) as details:
        details.add(links)
        return details.finish()


class Throttle:
    """How many detail pages one session fetches at once: all *most* workers
    while rrk.ir answers; halved when its connections keep dropping (*burst*
    retries within *window* seconds, at most one cut per window), then one
    more per *recover* pages fetched without trouble.  Never above *most*:
    it only ever lowers the load on the site."""

    def __init__(self, most: int, *, burst: int = 3, window: float = 30.0, recover: int = 50) -> None:
        self.most = self.allowed = most
        self.burst, self.window, self.recover = burst, window, recover
        self.busy = self.calm = 0
        self.retries: deque[float] = deque()
        self.cut_at = float("-inf")
        self.changed = threading.Condition()

    def __enter__(self) -> None:
        with self.changed:
            while self.busy >= self.allowed:
                self.changed.wait()
            self.busy += 1

    def __exit__(self, failed, *_: object) -> None:
        with self.changed:
            self.busy -= 1
            self.calm = 0 if failed else self.calm + 1
            if self.calm >= self.recover and self.allowed < self.most:
                self.allowed, self.calm = self.allowed + 1, 0
                if self.allowed == self.most:
                    log.info("detail pages: back to %d at once", self.most)
            self.changed.notify_all()

    def trouble(self) -> None:
        """A request is being retried (from any worker's thread)."""
        now = time.monotonic()
        with self.changed:
            self.retries.append(now)
            while self.retries[0] < now - self.window:
                self.retries.popleft()
            self.calm = 0
            if len(self.retries) >= self.burst and now - self.cut_at >= self.window and self.allowed > 1:
                self.allowed, self.cut_at = max(1, self.allowed // 2), now
                log.warning("detail pages: connections dropping, %d at once (of %d)", self.allowed, self.most)


class DetailFetcher:
    """Detail pages fetched by *workers* threads while links keep arriving,
    fewer at once while the site drops connections (:class:`Throttle`).

    Links only work in the session that listed them, so each worker uses its
    own tab of *client*'s session.  The main thread does every database
    write (in :meth:`add` and :meth:`finish`).  The first failure stops new
    fetches; pages already fetched are still saved, then the error is raised.
    A blocked request makes the firewall react, so nothing is retried here."""

    def __init__(self, client: RRSClient, repo: AdRepository, *, workers: int, delay: float,
                 max_attempts: int, limit: int | None = None) -> None:
        self.client, self.repo, self.delay, self.limit = client, repo, delay, limit
        self.max_attempts = max_attempts
        self.throttle = Throttle(workers)
        # Codes this fetcher took; stored ones are looked up per page (every stored code in each
        # session's memory took ~250 MB a session at 2.9M texts: 6 GB in a run of 16 sessions).
        self.done: set[str] = set()
        self.pool = ThreadPoolExecutor(workers)
        self.futures: dict = {}
        self.stop = threading.Event()
        self.tabs = threading.local()
        self.stored = 0
        self.error: Exception | None = None
        log.info("fetching detail pages with %d worker(s)", workers)

    def add(self, links: dict[str, str]) -> None:
        """Queue the new codes of *links*, and save what workers finished."""
        if self.error is not None:  # stop the listing too
            raise self.error
        new = [code for code in links if code not in self.done]
        finished = self.repo.finished_detail_codes(self.max_attempts, new)  # stored, or failed too often
        for code in new:
            if self.limit is not None and len(self.futures) >= self.limit:
                break
            self.done.add(code)
            if code not in finished:
                self.futures[self.pool.submit(self._fetch, code, links[code])] = code
        self._save(f for f in list(self.futures) if f.done())

    def wait_below(self, backlog: int) -> None:
        """Save finished pages until at most *backlog* are queued."""
        while len(self.futures) > backlog and self.error is None:
            finished, _ = wait(list(self.futures), return_when=FIRST_COMPLETED)
            self._save(finished)
        if self.error is not None:
            raise self.error

    def finish(self) -> int:
        """Wait for every queued page; returns the pages stored."""
        self._save(as_completed(list(self.futures)))
        if self.error is not None:
            log.info("saved %d detail pages before stopping", self.stored)
            raise self.error
        return self.stored

    def _fetch(self, code: str, link: str) -> AdDetail | None:
        if self.stop.is_set():
            return None
        if hasattr(self.tabs, "tab"):
            time.sleep(self.delay)
        else:
            self.tabs.tab = self.client.session.fork()
            self.tabs.tab.on_retry = self.throttle.trouble
        try:
            with self.throttle:
                if self.stop.is_set():  # stopped while waiting for a turn
                    return None
                return parse_detail_page(self.tabs.tab.get(link).text, code)
        except Exception:
            self.stop.set()
            raise

    def _save(self, finished) -> None:
        for future in finished:
            code = self.futures.pop(future)
            self.done.add(code)
            try:
                detail = future.result()
            except CancelledError:  # never started: its session stopped, not the page's fault
                continue
            except Exception as exc:
                # Network trouble isn't the page's fault: only a wrong page counts.
                if not isinstance(exc, requests.RequestException):
                    self.repo.record_failure(code, f"{type(exc).__name__}: {exc}")
                self.error = self.error or exc
                continue
            if detail is None:  # skipped after another worker failed
                continue
            self.repo.save_detail(code, detail, datetime.now().isoformat(timespec="seconds"))
            self.stored += 1
            if self.stored % 100 == 0:
                log.info("stored %d detail pages (%d queued)", self.stored, len(self.futures))

    def __enter__(self) -> "DetailFetcher":
        return self

    def __exit__(self, *_: object) -> None:
        # On any exit (listing failure too): no new fetches, keep what finished.
        self.stop.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self._save(f for f in list(self.futures) if f.done())
