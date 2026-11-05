import random
from dataclasses import replace
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from database.connection import connect
from database.repository import AdRepository
from scraper import windows
from scraper.pipeline import list_window, scrape_listing
from scraper.windows import (CAP, PICKS, STATUS, Window, jalali_today, new_windows, undated_windows, within,
                             year_bounds)
from test_listing import ROW, response

LETTER = ("P199_SABTNODATE_AZ", "P199_SABTNODATE_TA")
STATUSES = {"2": "آماده ارسال به چاپ", "3": "در حال انتشار امضا نشده", "4": "انتشار یافته امضا نشده",
            "5": "انتشار یافته امضا شده"}
PAPER = ("P199_NEWSPAPERDATE_AZ", "P199_NEWSPAPER_TA")


def test_split_goes_years_months_days_then_newspaper_date(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    [years] = Window().split()
    assert years.filters() == {LETTER[0]: "1300/01/01", LETTER[1]: "1406/01/01"}
    assert [w.filters()[LETTER[1]] for w in years.split()] == ["1353/01/01", "1406/01/01"]

    # One year is the same query as its 12 months: split straight into 2 x 6 months.
    assert [w.filters() for w in Window("years", 1403, 1404).split()] == [
        {LETTER[0]: "1403/01/01", LETTER[1]: "1403/07/01"},
        {LETTER[0]: "1403/07/01", LETTER[1]: "1404/01/01"}]
    # One month (Esfand) is the same query as its days: split straight into two halves.
    assert [w.filters() for w in Window("months", 1403 * 12 + 11, 1403 * 12 + 12).split()] == [
        {LETTER[0]: "1403/12/01", LETTER[1]: "1403/12/14"},
        {LETTER[0]: "1403/12/15", LETTER[1]: "1404/01/01"},  # never 1403/12/30
    ]
    # The last days don't overlap the ones before.
    assert [w.filters() for w in Window("days", 28, 29, 1403 * 12).split()] == [
        {LETTER[0]: "1403/01/28", LETTER[1]: "1403/01/28"},
        {LETTER[0]: "1403/01/29", LETTER[1]: "1403/02/01"}]

    one_day = Window("days", 5, 5, 1300 * 12 + 1)
    [by_paper] = one_day.split()
    assert by_paper.filters() == {LETTER[0]: "1300/02/05", LETTER[1]: "1300/02/05",
                                  PAPER[0]: "1300/01/01", PAPER[1]: "1406/01/01"}
    assert Window("days", 5, 5, 1390 * 12, dim=1, fixed=by_paper.fixed).split() == []  # one day of both dates

    # Saved for --resume: each kind of window comes back the same.
    dates = Window("dates", dim=1, fixed=tuple(one_day.filters().items()), days=("1390/01/01", "1390/02/03"))
    for window in (Window(), years, one_day, by_paper, dates):
        assert Window.from_json(window.to_json()) == window


def test_a_crowded_day_29_splits_into_its_real_days_then_by_newspaper_date_near_the_letter(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    span = lambda w, dim=LETTER: f"{w.filters()[dim[0]]}-{w.filters()[dim[1]]}"
    # "29 to the next 1st", still capped: Khordad has 31 days, Aban 30; never an invalid date.
    assert [span(w) for w in Window("days", 29, 29, 1402 * 12 + 2).split()] == [
        "1402/03/29-1402/03/29", "1402/03/30-1402/03/30", "1402/03/31-1402/04/01"]
    assert [span(w, PAPER) for w in Window("days", 29, 29, 1402 * 12 + 7, dim=1).split()] == [
        "1402/08/29-1402/08/29", "1402/08/30-1402/09/01"]
    # One letter day: the newspaper dates from its month to 13 months on, and before and after them.
    day = Window("day", 30, 30, 1402 * 12 + 2)
    assert [span(w, PAPER) for w in day.split()] == [
        "1300/01/01-1402/03/01", "1402/03/01-1403/04/01", "1403/04/01-1406/01/01"]
    assert {span(w) for w in day.split()} == {"1402/03/30-1402/03/30"}
    # Esfand's 29 or 30 stay together; a recent letter has nothing after its 13 months.
    assert [span(w, PAPER) for w in Window("days", 29, 29, 1405 * 12 + 11).split()] == [
        "1300/01/01-1405/12/01", "1405/12/01-1406/01/01"]
    for window in (day, Window("day", 31, 32, 1402 * 12 + 2)):
        assert Window.from_json(window.to_json()) == window and window.split()


def test_new_ads_are_listed_by_newspaper_date_from_a_week_before_the_newest(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    span = lambda w, dim=PAPER: f"{w.filters()[dim[0]]}-{w.filters()[dim[1]]}"
    assert [span(w) for w in new_windows("1405/08/09")] == ["1405/08/02-1405/09/01", "1405/09/01-1406/01/01"]
    assert [span(w) for w in new_windows("1405/08/03")] == ["1405/07/26-1405/08/01", "1405/08/01-1406/01/01"]
    assert [span(w) for w in new_windows("1405/12/20")] == ["1405/12/13-1406/01/01"]
    assert new_windows(None) == [Window()]
    # A crowded newspaper day: by letter date, the 13 months up to it first, then before and after.
    day = Window("day", 5, 5, 1405 * 12 + 7, dim=1)
    assert [span(w, LETTER) for w in day.split()] == [
        "1300/01/01-1404/08/01", "1404/08/01-1405/09/01", "1405/09/01-1406/01/01"]
    assert {span(w) for w in day.split()} == {"1405/08/05-1405/08/05"}
    assert len(jalali_today()) == 10 and jalali_today() < "1406"


def test_a_day_capped_on_both_dates_splits_into_real_days_then_by_newspaper_type_then_city():
    choices = {PICKS[0]: ["1", "2", "3", "4"], PICKS[1]: ["124", "439"]}
    span = lambda w, dim: f"{w.filters()[dim[0]]}-{w.filters()[dim[1]]}"
    letter = tuple(Window("day", 19, 19, 1405 * 12 + 6).filters().items())
    # The newspaper's last day to the next 1st: each day.
    month_end = Window("day", 31, 32, 1405 * 12 + 5, dim=1, fixed=letter)
    assert [span(w, PAPER) for w in month_end.split(choices)] == ["1405/06/31-1405/06/31", "1405/07/01-1405/07/01"]
    # A letter's, as queued before 2026-11-01: the same; Esfand's 29 to the 1st stays whole.
    for az, ta, parts in (("1405/06/31", "1405/07/01", ["1405/06/31-1405/06/31", "1405/07/01-1405/07/01"]),
                          ("1404/08/30", "1404/09/01", ["1404/08/30-1404/08/30", "1404/09/01-1404/09/01"]),
                          ("1404/12/29", "1405/01/01", ["1404/12/29-1405/01/01"] * 4)):
        queued = Window("day", 2, 2, 1405 * 12 + 6, dim=1, fixed=((LETTER[0], az), (LETTER[1], ta)))
        assert [span(w, LETTER) for w in queued.split(choices)] == parts
    # Then by newspaper type, then by city; nothing after both, or without the choices.
    day = Window("day", 21, 21, 1405 * 12 + 6, dim=1, fixed=letter)
    by_type = day.split(choices)
    assert [w.filters()[PICKS[0]] for w in by_type] == ["1", "2", "3", "4"]
    by_city = by_type[3].split(choices)
    assert [(w.filters()[PICKS[0]], w.filters()[PICKS[1]]) for w in by_city] == [("4", "124"), ("4", "439")]
    assert by_city[0].split(choices) == [] and day.split() == []
    assert Window.from_json(by_city[1].to_json()) == by_city[1]


def test_a_crowded_day_of_both_dates_loses_no_row(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    choices = {PICKS[0]: ["1", "2", "3", "4"], PICKS[1]: ["124", "200", "201", "202"]}

    def ad(code, kind, city, letter="1405/07/19", paper="1405/07/21"):
        r = row(code, letter, paper)
        r[12] = {"v": kind, "d": "x"}
        return r + [city]
    rows = [ad(n, "3", "124") for n in range(700)]  # Tehran: under the cap alone
    rows += [ad(1000 + n, "4", str(200 + n % 3)) for n in range(1500)]  # provinces: by city
    # A month's last letter day to the next 1st, capped on one newspaper day: each day.
    rows += [ad(3000 + n, "3", "124", "1405/07/01", "1405/07/02") for n in range(600)]
    rows += [ad(4000 + n, "3", "124", "1405/06/31", "1405/07/02") for n in range(600)]
    # Not printed yet, or published without a newspaper date, on the crowded letter day: by status.
    for n, status in enumerate(["2", "3", "4"]):
        rows.append(ad(6000 + n, None, None, paper=None))
        rows[-1][11] = STATUSES[status]
    site, repo, links = fake_site(rows), AdRepository(connect(":memory:")), {}

    todo = [Window(), *undated_windows()]
    while todo:
        todo += list_window(site, repo, todo.pop(), ad_type="2", page_size=100, seen_at="now", links=links,
                            choices=choices)

    assert set(links) == {r[9].split("p28_code=")[1].split("&")[0] for r in rows}


def test_a_run_keeps_to_its_letter_years(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    bounds = year_bounds("1395-1405")
    assert bounds == ("1395/01/01", "1406/01/01")
    for bad in ("1405-1395", "1395", "1299-1300"):
        with pytest.raises(ValueError):
            year_bounds(bad)
    letters = lambda found: {(w.filters()[LETTER[0]], w.filters()[LETTER[1]]) for w in found}
    # Saved windows: inside, outside (kept for later), or split in both, without a query; a window that
    # ends on the first day is outside (the next one has that day too).
    saved = [Window("years", 1390, 1400), Window("years", 1394, 1395), Window("day", 5, 5, 1401 * 12),
             Window("days", 1, 29, 1390 * 12)]
    inside, outside = within(saved, bounds)
    assert letters(inside) == {("1395/01/01", "1400/01/01"), ("1401/01/05", "1401/01/05")}
    assert letters(outside) == {("1390/01/01", "1395/01/01"), ("1394/01/01", "1395/01/01"), ("1390/01/01", "1390/02/01")}
    # The whole listing: its years only.
    assert {a for a, _ in letters(within([Window()], bounds)[0])} >= {"1395/01/01"}
    assert all(a >= "1395/01/01" and b <= "1406/01/01" for a, b in letters(within([Window()], bounds)[0]))
    # By newspaper date alone (--new-only): its letter dates clamped; a crowded day's letter windows outside dropped.
    [new] = within([Window("days", 20, 29, 1405 * 12 + 7, dim=1)], bounds)[0]
    assert (new.filters()[LETTER[0]], new.filters()[LETTER[1]]) == bounds and Window.from_json(new.to_json()) == new
    parts = replace(Window("day", 21, 21, 1405 * 12 + 7, dim=1), bounds=bounds).split()
    assert parts and all(a >= "1395/01/01" and b <= "1406/01/01" for a, b in letters(parts))


def test_a_listing_kept_to_some_years_stores_them_all(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(5)
    date = lambda y1, y2: f"{rnd.randint(y1, y2)}/{rnd.randint(1, 12):02d}/{rnd.randint(1, 29):02d}"
    rows = [row(n, date(1390, 1405), date(1390, 1405)) for n in range(3000)]
    rows += [row(4000 + n, "1399/05/05", date(1399, 1400)) for n in range(1200)]  # a crowded letter day
    rows += [row(6000 + n, "1300/02/01", date(1390, 1404)) for n in range(500)]  # no letter date: 1300
    site, repo, links = fake_site(rows), AdRepository(connect(":memory:")), {}

    todo = within([Window(), *undated_windows()], year_bounds("1398-1401"))[0]
    while todo:
        todo += list_window(site, repo, todo.pop(), ad_type="2", page_size=100, seen_at="now", links=links)

    code = lambda r: r[9].split("p28_code=")[1].split("&")[0]
    assert {code(r) for r in rows if "1398/01/01" <= r[2] < "1402/01/01"} <= set(links)
    assert set(links) <= {code(r) for r in rows if "1398/01/01" <= r[2] <= "1402/01/01"}  # its last day's next


def fake_site(rows):
    """iter_pages over *rows*, filtered like the server and capped at 999."""
    def iter_pages(noe_agahi, page_size, filters=None, **_):
        found = rows
        for (az, ta), column in ((LETTER, 2), (PAPER, 5)):
            if filters and az in filters:
                found = [r for r in found if r[column] and filters[az] <= r[column] <= filters[ta]]
        for item, cell in zip(PICKS, (lambda r: r[12]["v"], lambda r: r[16] if len(r) > 16 else None)):
            if filters and item in filters:
                found = [r for r in found if cell(r) == filters[item]]
        if filters and STATUS in filters:
            found = [r for r in found if r[11] == STATUSES[filters[STATUS]]]
        total = min(len(found), CAP)
        for first in range(0, total, page_size):
            values = found[first:min(first + page_size, total)]
            payload = response(*values, more=first + page_size < total, total=total)
            payload["regions"][0]["fetchedData"]["firstRow"] = first + 1
            yield payload
    return SimpleNamespace(session=SimpleNamespace(archive=None), iter_pages=iter_pages)


def row(code, letter, paper):
    r = list(ROW)
    r[2], r[5] = letter, paper
    r[9] = r[10] = f'<a href="/ords/f-detail-ad?p28_code={code}&cs=x">'
    return r


def test_every_row_under_the_cap_is_collected(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(4)
    date = lambda y1, y2: f"{rnd.randint(y1, y2)}/{rnd.randint(1, 12):02d}/{rnd.randint(1, 31 if rnd.random() < .1 else 29):02d}"
    rows = [row(n, date(1395, 1405), date(1395, 1405)) for n in range(1500)]
    rows += [row(2000 + n, "1300/02/01", date(1382, 1404)) for n in range(1200)]  # crowded placeholder day
    lost = [row(9000 + n, "1300/02/01", None) for n in range(5)]  # no newspaper date: unreachable
    repo = AdRepository(connect(":memory:"))

    links = scrape_listing(fake_site(rows + lost), repo, ad_type="4", page_size=100)

    stored = {r["detail_code"] for r in repo.all_ads()}
    assert stored == set(links) == {str(n) for n in range(1500)} | {str(2000 + n) for n in range(1200)}


def test_crowded_letter_days_lose_no_row(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(7)
    paper = lambda: f"{rnd.choice([1402, 1402, 1403, 1404])}/{rnd.randint(1, 12):02d}/{rnd.randint(1, 29):02d}"
    rows = [row(n, "1402/03/30", "1402/03/30" if n < 900 else paper()) for n in range(1100)]  # one crowded day
    rows += [row(2000, "1402/03/30", "1401/11/05")]  # printed before its letter (a typo, but in the data)
    rows += [row(3000 + n, f"1402/08/{29 + n % 2}", "1402/09/02") for n in range(1200)]  # a crowded "29 to the 1st"
    repo = AdRepository(connect(":memory:"))

    links = scrape_listing(fake_site(rows), repo, ad_type="2", page_size=100)

    assert set(links) == {r[9].split("p28_code=")[1].split("&")[0] for r in rows}


def test_new_ads_are_found_whatever_their_letter_date(monkeypatch):
    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(3)
    old = [row(n, "1404/03/10", f"1405/0{rnd.randint(1, 7)}/{rnd.randint(1, 29):02d}") for n in range(500)]
    letter = lambda: rnd.choice(["1300/02/01", "1390/01/05", "1405/07/30", f"1405/08/{rnd.randint(1, 20):02d}"])
    new = [row(1000 + n, letter(), "1405/08/12") for n in range(1200)]  # a crowded newspaper day
    new += [row(3000 + n, letter(), f"1405/08/{2 + n % 25:02d}") for n in range(300)]
    site, repo, links = fake_site(old + new), AdRepository(connect(":memory:")), {}

    todo = new_windows("1405/08/09")
    while todo:
        todo += list_window(site, repo, todo.pop(), ad_type="2", page_size=100, seen_at="now", links=links)

    assert set(links) == {r[9].split("p28_code=")[1].split("&")[0] for r in new}  # none printed before 1405/08/02


def test_a_window_capped_on_one_day_of_both_dates_stays_among_the_windows_left(monkeypatch, tmp_path):
    from scraper.pipeline import scrape_type

    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rows = [row(n, "1402/03/10", "1402/03/12") for n in range(1100)] + [row(2000, "1402/05/01", "1402/05/03")]
    saved = []
    new_repo = lambda: AdRepository(connect(tmp_path / "t.db"))
    scrape_type(lambda *_: nullcontext(fake_site(rows)), new_repo, "2", sessions=1, attempts=1, retry_wait=0,
                page_size=100, workers=1, delay=0, max_attempts=3, detail_limit=0, save_left=saved.append)

    [capped] = saved[-1]
    assert capped.filters() == {LETTER[0]: "1402/03/10", LETTER[1]: "1402/03/10",
                                PAPER[0]: "1402/03/12", PAPER[1]: "1402/03/12"}
    assert len(list(new_repo().all_ads())) == CAP + 1  # its first 999 rows, and the rest of the listing


def test_sessions_share_the_windows_and_fetch_only_their_own_links(monkeypatch, tmp_path):
    import threading
    from contextlib import nullcontext

    from scraper.pipeline import scrape_type
    from test_parser import page

    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(9)
    date = lambda: f"{rnd.randint(1395, 1405)}/{rnd.randint(1, 12):02d}/{rnd.randint(1, 29):02d}"
    rows = [row(n, date(), date()) for n in range(3000)]
    queries, lock = {}, threading.Lock()

    def new_client(session, attempt):
        site = fake_site(rows)

        def iter_pages(**kwargs):
            for payload in site.iter_pages(**kwargs):
                with lock:
                    queries[session] = queries.get(session, 0) + 1
                for r in payload["regions"][0]["fetchedData"]["values"]:  # this session's links
                    r[9] = r[10] = r[10].replace("&cs=x", f"&session={session}&cs=x")
                yield payload

        def get(link):  # a link from another session would be refused
            assert f"session={session}&" in link, "link used outside its session"
            return SimpleNamespace(text=page(link.split("p28_code=")[1].split("&")[0]))

        client = SimpleNamespace(session=SimpleNamespace(archive=None, fork=lambda: SimpleNamespace(get=get)),
                                 iter_pages=iter_pages)
        return nullcontext(client)

    new_repo = lambda: AdRepository(connect(tmp_path / "t.db"))
    scrape_type(new_client, new_repo, "9", sessions=3, attempts=1, retry_wait=0, page_size=100,
                workers=2, delay=0, max_attempts=3, detail_limit=None)

    repo = new_repo()
    assert {r["detail_code"] for r in repo.all_ads()} == {str(n) for n in range(3000)}
    assert repo.codes_without_detail(3) == []
    assert len(queries) == 3  # every session took windows


def test_a_stopped_type_resumes_from_the_windows_it_left(monkeypatch, tmp_path):
    import requests

    from scraper.pipeline import scrape_type

    monkeypatch.setattr(windows, "_end_year", lambda: 1406)
    rnd = random.Random(5)
    date = lambda: f"{rnd.randint(1395, 1405)}/{rnd.randint(1, 12):02d}/{rnd.randint(1, 29):02d}"
    rows = [row(n, date(), date()) for n in range(3000)]
    saved = []

    def new_client(pages_before_failing):
        def make(session, attempt):
            site, served = fake_site(rows), [0]

            def iter_pages(**kwargs):
                for payload in site.iter_pages(**kwargs):
                    served[0] += 1
                    if served[0] > pages_before_failing:  # the network goes down mid-window
                        raise requests.ConnectionError("gone")
                    yield payload
            return nullcontext(SimpleNamespace(session=SimpleNamespace(archive=None), iter_pages=iter_pages))
        return make

    new_repo = lambda: AdRepository(connect(tmp_path / "t.db"))
    args = dict(sessions=2, attempts=1, retry_wait=0, page_size=100, workers=1, delay=0,
                max_attempts=3, detail_limit=0, save_left=lambda left: saved.append(left))
    with pytest.raises(requests.ConnectionError):
        scrape_type(new_client(5), new_repo, "2", **args)
    left = saved[-1]
    assert left and Window() not in left  # some windows done, the rest saved, the one being listed too
    stored = {r["detail_code"] for r in new_repo().all_ads()}
    assert 0 < len(stored) < 3000

    scrape_type(new_client(10 ** 6), new_repo, "2", windows=[Window.from_json(w.to_json()) for w in left], **args)
    assert saved[-1] == []
    assert {r["detail_code"] for r in new_repo().all_ads()} == {str(n) for n in range(3000)}
