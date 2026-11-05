from dataclasses import replace
from types import SimpleNamespace

import pytest

from database.connection import connect
from database.repository import AdRepository
from models.advertisement import Advertisement
from parsing import DetailPageError
from scraper.pipeline import fetch_details, scrape_listing
from test_listing import ROW, response
from test_parser import page

AD = Advertisement.from_listing_row(ROW)


@pytest.fixture
def repo():
    return AdRepository(connect(":memory:"))


def test_upsert_keeps_first_seen_and_refreshes_the_rest(repo):
    assert repo.upsert_ads([AD], ad_type="12", seen_at="2026-10-29T04:00:00") == 1
    changed = replace(AD, status="changed")
    no_key = replace(AD, detail_code=None)
    assert repo.upsert_ads([changed, no_key], ad_type="12", seen_at="2026-10-30T04:00:00") == 1

    [row] = repo.all_ads()
    assert row["status"] == "changed"
    assert row["tracking_code"] == AD.tracking_code
    assert (row["first_seen"], row["last_seen"]) == ("2026-10-29T04:00:00", "2026-10-30T04:00:00")


def test_ad_types_keep_first_seen(repo):
    repo.save_ad_types([("12", "ابلاغ")], "t1")
    repo.save_ad_types([("12", "ابلاغ"), ("1", "تاسيس")], "t2")
    assert [tuple(r) for r in repo.conn.execute("SELECT code, first_seen, last_seen FROM ad_types ORDER BY rowid").fetchall()] == [
        ("12", "t1", "t2"), ("1", "t2", "t2")]


def test_pages_before_a_failure_stay_stored(repo):
    def iter_pages(**_):
        yield response(ROW, more=True, total=2)
        raise TimeoutError("page 2")

    client = SimpleNamespace(session=SimpleNamespace(archive=None), iter_pages=iter_pages)
    with pytest.raises(TimeoutError):
        scrape_listing(client, repo, ad_type="12", page_size=1)
    assert [row["detail_code"] for row in repo.all_ads()] == [AD.detail_code]


def listed(repo, *codes):
    repo.upsert_ads([replace(AD, detail_code=c) for c in codes], ad_type="12", seen_at="t")
    return {c: f"/ords/r/rrs/rrs-front/f-detail-ad?p28_code={c}&cs=x" for c in codes}


def detail_client(pages):
    """A client whose tabs answer from *pages*: code -> html."""
    def get(link):
        return SimpleNamespace(text=pages[link.split("p28_code=")[1].split("&")[0]])
    tab = SimpleNamespace(get=get)
    return SimpleNamespace(session=SimpleNamespace(fork=lambda: tab))


def test_details_are_stored_and_not_fetched_again(repo):
    links = listed(repo, "1", "2")
    del links["2"]  # not listed in this session: its old link would be refused
    client = detail_client({"1": page("1", text="full text")})
    args = dict(workers=1, delay=0, max_attempts=3)

    assert fetch_details(client, repo, links, **args) == 1
    assert repo.codes_without_detail(3) == ["2"]
    assert [(r["detail_code"], r["full_text"], r["company_name"]) for r in repo.all_ads()] == [
        ("1", "full text", "شرکت"), ("2", None, None)]
    assert fetch_details(client, repo, links, **args) == 0


def test_export_streams_the_same_json_gzipped(repo, tmp_path):
    import gzip
    import json

    path = tmp_path / "ads.json.gz"
    assert repo.export(path) == 0 and gzip.open(path, "rt").read() == "[]"
    links = listed(repo, "1", "2")
    fetch_details(detail_client({"1": page("1", text="full\ntext")}), repo, {"1": links["1"]},
                  workers=1, delay=0, max_attempts=3)

    assert repo.export(path) == 2
    ads = list(repo.all_ads())
    assert gzip.open(path, "rt", encoding="utf-8").read() == json.dumps(ads, ensure_ascii=False, indent=1)
    assert not path.with_name("ads.json.gz.part").exists()


def test_a_blocked_page_stops_the_run_and_counts_as_an_attempt(repo):
    links = listed(repo, "1", "2")
    client = detail_client({"1": "<html>Web Page Blocked</html>", "2": page("2")})

    with pytest.raises(DetailPageError, match="not its detail page"):
        fetch_details(client, repo, links, workers=1, delay=0, max_attempts=3)
    assert repo.codes_without_detail(3) == ["1", "2"]  # "2" never fetched
    assert repo.codes_without_detail(1) == ["2"]  # "1" failed once


def test_workers_fetch_at_the_same_time_and_every_ad_is_stored(repo):
    import threading

    codes = [str(c) for c in range(1, 10)]
    links = listed(repo, *codes)
    # The first 3 requests only return once all 3 are in flight together.
    together = threading.Barrier(3, timeout=5)
    calls, tabs = iter(range(100)), []

    def fork():
        def get(link):
            if next(calls) < 3:
                together.wait()
            return SimpleNamespace(text=page(link.split("p28_code=")[1].split("&")[0]))
        tabs.append(1)
        return SimpleNamespace(get=get)

    client = SimpleNamespace(session=SimpleNamespace(fork=fork))
    assert fetch_details(client, repo, links, workers=3, delay=0, max_attempts=3) == 9
    assert repo.codes_without_detail(3) == []
    assert len(tabs) == 3  # one tab of the listing session per worker


def test_details_start_while_the_listing_is_still_running(tmp_path):
    import threading
    from contextlib import nullcontext

    from scraper.pipeline import scrape_type
    from test_windows import row

    first_detail = threading.Event()

    def iter_pages(**_):
        yield response(row(1, "1403/01/01", None), more=True, total=2)
        assert first_detail.wait(5), "no detail page was fetched before page 2"
        yield response(row(2, "1403/01/02", None), total=2)

    def get(link):
        first_detail.set()
        return SimpleNamespace(text=page(link.split("p28_code=")[1].split("&")[0]))

    client = SimpleNamespace(session=SimpleNamespace(archive=None, fork=lambda: SimpleNamespace(get=get)),
                             iter_pages=iter_pages)
    new_repo = lambda: AdRepository(connect(tmp_path / "t.db"))
    assert scrape_type(lambda *_: nullcontext(client), new_repo, "12", sessions=1, attempts=1,
                       retry_wait=0, page_size=1, workers=2, delay=0, max_attempts=3,
                       detail_limit=None) == (2, 2)
    assert new_repo().codes_without_detail(3) == []


def test_compact_packs_texts_and_drops_summaries(tmp_path):
    from database.compact import compact
    from models.advertisement import AdDetail
    db = tmp_path / "rrk.db"
    repo = AdRepository(connect(db))
    listed(repo, "1", "2")
    detail = AdDetail(full_text="برابر صورتجلسه مجمع عمومی " * 20, company_name="شرکت",
                      registration_number=None, publication_count=None, issuer=None)
    repo.save_detail("1", detail, "t")  # stored plain: no dictionary yet
    repo.conn.close()

    compact(db)
    repo = AdRepository(connect(db))
    assert repo.conn.execute("SELECT typeof(full_text) FROM details").fetchone()[0] == "blob"
    assert [(r["detail_code"], r["full_text"], r["summary"]) for r in repo.all_ads()] == [
        ("1", detail.full_text, None), ("2", None, AD.summary)]
    repo.save_detail("2", replace(detail, full_text="تست"), "t")  # packed with the trained dictionary
    listed(repo, "2")  # a new listing doesn't bring the summary back
    assert [(r["full_text"], r["summary"]) for r in repo.all_ads()][1] == ("تست", None)
    assert repo.conn.execute("SELECT count(*) FROM details WHERE typeof(full_text) = 'text'").fetchone()[0] == 0
