import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime
from pathlib import Path

import config
from database.connection import connect
from database.repository import AdRepository
from rrs.client import RRSClient
from parsing import DetailPageError
from scraper.pipeline import scrape_type
from scraper.windows import (PICKS, Window, gap_windows, jalali_today, new_windows, undated_windows, within,
                             year_bounds)

parser = argparse.ArgumentParser(description="Scrape the rrk.ir open-data listing.")
parser.add_argument("--debug", action="store_true",
                    help="log every HTTP call with its essential values, and save "
                         "every server response under debug/<time>/")
parser.add_argument("--type", default=config.AD_TYPE,
                    help=f"P199_NOE_AGAHI codes, comma separated, or 'all'; default {config.AD_TYPE}")
parser.add_argument("--types-at-once", type=int, default=config.TYPES_AT_ONCE,
                    help=f"ad types scraped at the same time, each in its own session; default {config.TYPES_AT_ONCE}")
parser.add_argument("--page-size", type=int, default=config.PAGE_SIZE)
parser.add_argument("--timeout", type=float, default=config.TIMEOUT,
                    help="seconds to wait for each response")
parser.add_argument("--page-load-calls", action="store_true",
                    help="also make the browser's 4 page-load AJAX calls on the search and results pages")
parser.add_argument("--human-pacing", action="store_true",
                    help="pause like a person (~23 s on home, ~7.5 s before searching)")
parser.add_argument("--verify-ssl", action="store_true",
                    help="check rrk.ir's TLS certificate (off until a run shows its chain verifies)")
parser.add_argument("--no-tcp-keepalive", action="store_true",
                    help="plain sockets: idle connections to rrk.ir then die silently after ~30 s")
parser.add_argument("--db", type=Path, default=config.DB_PATH,
                    help=f"SQLite file the ads are stored in, default {config.DB_PATH}")
parser.add_argument("--sessions-per-type", type=int, default=config.SESSIONS_PER_TYPE,
                    help="sessions sharing one ad type's date windows, each with its own detail"
                         f" workers; default {config.SESSIONS_PER_TYPE}")
parser.add_argument("--workers", type=int, default=config.WORKERS,
                    help=f"detail pages fetched at once per session, default {config.WORKERS}")
parser.add_argument("--detail-delay", type=float, default=config.DETAIL_DELAY,
                    help=f"seconds each detail worker waits between pages, default {config.DETAIL_DELAY}")
parser.add_argument("--page-delay", type=float, default=config.PAGE_DELAY,
                    help=f"seconds a session waits between two grid pages, default {config.PAGE_DELAY}")
parser.add_argument("--detail-backlog", type=int, default=config.DETAIL_BACKLOG,
                    help="a session lists on only while at most this many of its detail links"
                         f" wait for a worker (stopping loses the rest), default {config.DETAIL_BACKLOG}")
parser.add_argument("--no-detail-backlog", action="store_true",
                    help="list on regardless of waiting detail links (a stop then needs --missing-only"
                         " for many more of them)")
parser.add_argument("--missing-only", action="store_true",
                    help="list only the dates of stored ads still without a detail page (fetches"
                         " their fresh links; new ads elsewhere are not listed)")
parser.add_argument("--resume", action="store_true",
                    help="list only the date windows a stopped run of each type left unfinished"
                         " (with --missing-only: those and the dates of ads without a detail page)")
parser.add_argument("--new-only", action="store_true",
                    help="list only the newspaper dates from a week before the newest stored one on:"
                         " the notices printed since, whatever their letter date")
parser.add_argument("--years", type=year_bounds, default=(), metavar="FROM-TO",
                    help="only notices whose letter date is in these years, such as 1395-1405 (1300: the"
                         " ones without a letter date); saved windows outside them wait for a later run")
parser.add_argument("--detail-limit", type=int, default=None,
                    help="fetch at most this many detail pages (0: none), default all missing")
parser.add_argument("--export", action="store_true",
                    help="only write every stored ad, with its detail page, to ads.json.gz beside"
                         " the database (no requests to rrk.ir)")
args = parser.parse_args()

logging.basicConfig(
    level=logging.DEBUG if args.debug else logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)-11s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("charset_normalizer").setLevel(logging.WARNING)
if not args.debug:
    logging.getLogger("urllib3").setLevel(logging.WARNING)

archive_dir = Path("debug") / datetime.now().strftime("%Y%m%d-%H%M%S") if args.debug else None
if archive_dir:
    logging.getLogger("rrk").info("saving every server response to %s/", archive_dir)

log = logging.getLogger("rrk")

if args.export:
    path = args.db.with_name("ads.json.gz")
    print(f"exported {AdRepository(connect(args.db)).export(path)} advertisements from {args.db} to {path}")
    raise SystemExit


def new_client(name: str) -> RRSClient:
    return RRSClient(timeout=args.timeout,
                     archive_dir=archive_dir / name if archive_dir else None,
                     page_load_calls=args.page_load_calls,
                     human_pacing=args.human_pacing,
                     tcp_keepalive=not args.no_tcp_keepalive,
                     verify_ssl=args.verify_ssl)


def run_type(ad_type: str) -> tuple[int, int]:
    repo = AdRepository(connect(args.db))
    saved = repo.windows_left(ad_type)
    bounded = (lambda found: within(found, args.years)[0]) if args.years else list
    resumed, later = [Window.from_json(spec) for spec in saved], []
    if args.years:  # saved windows (parts) outside the years wait for a later run, whatever the mode
        resumed, outside = within(resumed, args.years)
        later = [window.to_json() for window in outside]
    windows = resumed if args.resume else []
    if args.resume:
        log.info("type %s: resuming %d date window(s) a stopped run left%s", ad_type, len(windows),
                 f", {len(later)} outside the years kept for later" if args.years else "")
    if args.missing_only:
        gaps = repo.detail_gaps(ad_type, config.MAX_ATTEMPTS)
        windows += (gap := bounded(gap_windows(gaps)))
        windows += bounded(undated_windows())  # ads without a newspaper date that crowded letter days hide
        log.info("type %s: %d ads without a detail page, in %d date windows%s",
                 ad_type, sum(g[3] for g in gaps), len(gap), " in the years" if args.years else "")
    if args.new_only:
        windows += bounded(new_windows(newest := repo.newest_newspaper_date(ad_type, jalali_today())))
        log.info("type %s: %s", ad_type, f"listing what was printed from a week before {newest} on" if newest
                 else "no newspaper date stored yet: listing everything")
    if not (args.resume or args.missing_only or args.new_only):
        windows = bounded([Window(), *undated_windows()])
    if args.years:
        log.info("type %s: only letter dates from %s, before %s", ad_type, *args.years)
    # Windows this run won't list stay saved: a full listing replaces only those inside its years.
    kept = saved if (args.missing_only or args.new_only) and not args.resume else later

    def save_left(left: list[Window]) -> None:  # from any session's thread: a connection of its own
        with closing(connect(args.db)) as conn:
            AdRepository(conn).save_windows_left(ad_type, dict.fromkeys(kept + [w.to_json() for w in left]))

    # Own sessions (detail links only work there), each with its own database connection.
    return scrape_type(lambda session, attempt: new_client(f"type-{ad_type}-s{session}-{attempt}"),
                       lambda: AdRepository(connect(args.db)), ad_type,
                       sessions=args.sessions_per_type,
                       attempts=config.TYPE_ATTEMPTS, retry_wait=config.SESSION_RETRY_WAIT,
                       page_size=args.page_size, workers=args.workers,
                       delay=args.detail_delay, max_attempts=config.MAX_ATTEMPTS,
                       detail_limit=args.detail_limit, backlog=None if args.no_detail_backlog else args.detail_backlog,
                       windows=windows, save_left=save_left, choices=choices, page_delay=args.page_delay)


repo = AdRepository(connect(args.db))
failed = False
try:
    with new_client("types") as client:  # the dropdown, every run: the list may change
        types = dict(client.fetch_ad_types())
        # A window capped on one day of both dates is split by these (~9 requests).
        choices = {item: [code for code, _ in client.fetch_choices(item)] for item in PICKS}
    repo.save_ad_types(types.items(), datetime.now().isoformat(timespec="seconds"))
    log.info("%d ad types: %s", len(types), ", ".join(f"{c} {l}" for c, l in types.items()))
    wanted = list(types) if args.type == "all" else args.type.split(",")
    unknown = [code for code in wanted if code not in types]
    if unknown:
        raise SystemExit(f"unknown ad type(s) {', '.join(unknown)}; the dropdown has {', '.join(types)}")

    with ThreadPoolExecutor(args.types_at_once) as pool:
        futures = {pool.submit(run_type, code): code for code in wanted}
        for future in as_completed(futures):
            code = futures[future]
            try:
                listed, stored = future.result()
                print(f"type {code} ({types[code]}): {listed} ads listed, {stored} detail pages stored")
            except Exception as exc:
                # Pages stored before the failure stay in the database.
                log.error("type %s stopped: %s: %s", code, type(exc).__name__, exc,
                          exc_info=args.debug)
                failed = True
                # A wrong page may be the firewall reacting: start no more types.
                # Network trouble only ends its own type (after its fresh sessions).
                if isinstance(exc, DetailPageError):
                    pool.shutdown(cancel_futures=True)
except Exception as exc:
    log.error("stopped: %s: %s", type(exc).__name__, exc, exc_info=args.debug)
    failed = True

given_up = repo.given_up_details(config.MAX_ATTEMPTS)
if given_up:
    log.warning("%d detail pages failed %d times and are skipped from now on, e.g. %s",
                len(given_up), config.MAX_ATTEMPTS, "; ".join(f"{c}: {e}" for c, _, e in given_up[:3]))
if failed:
    raise SystemExit(1)
