import logging
import random
import threading
from contextlib import nullcontext
from types import SimpleNamespace

from database.connection import connect
from database.repository import AdRepository
from models.advertisement import AdDetail
from scraper import windows
from scraper.pipeline import scrape_type
from scraper.windows import Window, gap_windows
from test_parser import page
from test_windows import LETTER, PAPER, fake_site, row

DETAIL = AdDetail("text", None, None, None, None)


def test_gaps_join_neighbouring_dates_within_the_budget():
    counts = [("1400/01/01", "1400/01/02", 300, 1),
              ("1400/01/02", "1400/01/03", 300, 0),  # nothing missing, but inside the range
              ("1400/01/03", "1400/01/04", 300, 2),
              ("1400/01/04", "1400/01/05", 300, 1),  # would make 1,200 rows: next window
              ("1400/01/05", None, 50, 0)]  # nothing missing after it: not listed
    assert [w.filters() for w in gap_windows(counts, budget=900)] == [
        {LETTER[0]: "1400/01/01", LETTER[1]: "1400/01/03"},
        {LETTER[0]: "1400/01/04", LETTER[1]: "1400/01/04"}]


def test_a_crowded_letter_date_is_reached_by_newspaper_date_alone(caplog):
    caplog.set_level(logging.INFO)
    counts = [("1299/12/29", "1300/01/01", 100, 1),
              ("1300/02/01", "1388/01/01", 500, 0),
              ("1300/02/01", "1388/01/02", 300, 4),
              ("1300/02/01", "1389/05/05", 950, 1),  # over budget even alone
              ("1300/02/01", None, 3, 2),  # no newspaper date: by status (undated_windows)
              ("1300/02/02", "1300/02/02", 10, 1)]
    found = [w.filters() for w in gap_windows(counts, budget=900)]
    placeholder = {LETTER[0]: "1300/02/01", LETTER[1]: "1300/02/01"}
    assert found == [
        {LETTER[0]: "1299/12/29", LETTER[1]: "1299/12/29"},  # never a range over the placeholder
        {**placeholder, PAPER[0]: "1388/01/02", PAPER[1]: "1388/01/02"},
        {**placeholder, PAPER[0]: "1389/05/05", PAPER[1]: "1389/05/05"},
        {LETTER[0]: "1300/02/02", LETTER[1]: "1300/02/02"}]  # and the run lists undated_windows()
    assert "2 ads without detail on crowded letter dates have no newspaper date" in caplog.text


def test_a_capped_gap_window_splits_its_dates_then_by_newspaper_date(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    gap = Window("dates", days=("1400/01/01", "1400/02/05", "1400/03/09"))
    assert [w.filters() for w in gap.split()] == [
        {LETTER[0]: "1400/01/01", LETTER[1]: "1400/01/01"},
        {LETTER[0]: "1400/02/05", LETTER[1]: "1400/03/09"}]
    before, near, after = Window("dates", days=("1400/01/01",)).split()  # printed near the letter, else before or after
    assert near.filters() == {LETTER[0]: "1400/01/01", LETTER[1]: "1400/01/01",
                              PAPER[0]: "1400/01/01", PAPER[1]: "1401/02/01"}
    assert (before.filters()[PAPER[0]], after.filters()[PAPER[1]]) == ("1300/01/01", "1406/01/01")


def test_detail_gaps_counts_what_a_run_still_fetches(tmp_path):
    repo = AdRepository(connect(tmp_path / "t.db"))
    from parsing import parse_listing_payload
    from test_listing import response
    rows = [row(1, "1400/01/01", "1400/01/02"), row(2, "1400/01/01", "1400/01/02"),
            row(3, "1400/01/01", None), row(4, "1401/01/01", None)]
    repo.upsert_ads(parse_listing_payload(response(*rows)), ad_type="9", seen_at="t")
    repo.upsert_ads(parse_listing_payload(response(row(5, "1400/01/01", None))), ad_type="4", seen_at="t")
    repo.save_detail("1", DETAIL, "t")
    for _ in range(3):
        repo.record_failure("4", "wrong page")  # given up: not missing
    assert sorted(repo.detail_gaps("9", max_attempts=3), key=str) == sorted([
        ("1400/01/01", "1400/01/02", 2, 1), ("1400/01/01", None, 1, 1),
        ("1401/01/01", None, 1, 0)], key=str)


def sessions(rows, queries, fetched):
    """new_client for scrape_type over *rows*; links carry their session."""
    lock = threading.Lock()

    def new_client(session, attempt):
        site = fake_site(rows)

        def iter_pages(**kwargs):
            for payload in site.iter_pages(**kwargs):
                with lock:
                    queries.append(kwargs.get("filters"))  # one per grid page
                for r in payload["regions"][0]["fetchedData"]["values"]:
                    r[9] = r[10] = r[10].replace("&cs=x", f"&session={session}&cs=x")
                yield payload

        def get(link):
            assert f"session={session}&" in link, "link used outside its session"
            code = link.split("p28_code=")[1].split("&")[0]
            with lock:
                fetched.append(code)
            return SimpleNamespace(text=page(code))

        return nullcontext(SimpleNamespace(
            session=SimpleNamespace(archive=None, fork=lambda: SimpleNamespace(get=get)),
            iter_pages=iter_pages))
    return new_client


ARGS = dict(attempts=1, retry_wait=0, page_size=100, delay=0, max_attempts=3, detail_limit=None)


def test_a_stopped_run_is_resumed_by_listing_only_the_gaps(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(11)
    date = lambda: f"{rnd.randint(1395, 1405)}/{rnd.randint(1, 12):02d}/{rnd.randint(1, 29):02d}"
    rows = [row(n, date(), date()) for n in range(4000)]
    rows += [row(5000 + n, "1300/02/01", date()) for n in range(1500)]  # crowded placeholder
    new_repo = lambda: AdRepository(connect(tmp_path / "t.db"))
    full = []
    scrape_type(sessions(rows, full, []), new_repo, "9", sessions=2, workers=2,
                **{**ARGS, "detail_limit": 0})  # listed, details "stopped" before any fetch
    repo = new_repo()
    # Fetched before the stop: all but the windows each session listed last.
    late = lambda ad: ad["letter_date"] >= "1404/06/01" or (
        ad["letter_date"] == "1300/02/01" and ad["newspaper_date"] >= "1405/01/01")
    done = [ad["detail_code"] for ad in repo.all_ads() if not late(ad)]
    for code in done:
        repo.save_detail(code, DETAIL, "t")
    missing = set(repo.codes_without_detail(3))
    rows += [row(9000 + n, rows[n][2], date()) for n in range(0, 4000, 4)]  # new ads since

    queries, fetched = [], []
    gaps = gap_windows(repo.detail_gaps("9", 3))
    scrape_type(sessions(rows, queries, fetched), new_repo, "9", sessions=3, workers=2,
                backlog=50, windows=gaps, **ARGS)

    assert missing <= set(fetched)  # every gap filled, each with a link of its own session
    assert not missing & set(done)
    assert len(missing) > 300 and len(queries) < len(full) / 4  # no full re-listing
    assert all(q for q in queries)  # never an unfiltered (whole type) query


def test_a_session_lists_on_only_when_its_backlog_is_small(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(3)
    date = lambda: f"{rnd.randint(1395, 1405)}/{rnd.randint(1, 12):02d}/{rnd.randint(1, 29):02d}"
    rows = [row(n, date(), date()) for n in range(2500)]
    queries, fetched, behind = [], [], []
    new_client = sessions(rows, queries, fetched)

    def watched(session, attempt):  # how far the details are behind at each new window
        with new_client(session, attempt) as client:
            pages = client.iter_pages

            def iter_pages(**kwargs):
                behind.append(len(listed - set(fetched)))
                for payload in pages(**kwargs):
                    listed.update(r[10].split("p28_code=")[1].split("&")[0]
                                  for r in payload["regions"][0]["fetchedData"]["values"])
                    yield payload
            client.iter_pages = iter_pages
            return nullcontext(client)

    listed = set()
    scrape_type(watched, lambda: AdRepository(connect(tmp_path / "t.db")), "9", sessions=1,
                workers=3, backlog=0, **ARGS)
    assert len(fetched) == 2500 and len(behind) > 3
    assert set(behind) == {0}
