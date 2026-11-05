from contextlib import nullcontext
from http.client import RemoteDisconnected
from types import SimpleNamespace

import pytest
import requests

from apex.session import ApexSession
from database.connection import connect
from database.repository import AdRepository
from parsing import DetailPageError
from scraper.pipeline import scrape_type
from test_listing import ROW, response

ABORTED = requests.ConnectionError(
    ("Connection aborted.", RemoteDisconnected("Remote end closed connection without response")))


def session_answering(*outcomes):
    """An ApexSession whose transport raises or answers *outcomes* in order."""
    session = ApexSession(base_url="https://rrk.ir")
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        ok = requests.Response()
        ok.status_code, ok.url, ok._content = 200, url, b"{}"
        return ok

    session._http.request = request
    return session, calls


def test_dropped_connection_is_retried_on_a_new_one(sleeps):
    session, calls = session_answering(ABORTED, requests.ConnectTimeout(), "ok")
    assert session.get("/ords/r/rrs/rrs-front/f-detail-ad").status_code == 200
    assert (calls, sleeps) == (["GET"] * 3, [2.0, 15.0])


def test_retries_run_out(sleeps):
    session, calls = session_answering(*[requests.ReadTimeout()] * 4)
    with pytest.raises(requests.ReadTimeout):
        session.post("/ords/wwv_flow.ajax", data={})
    assert len(calls) == 4


def test_a_page_submit_the_server_may_have_received_is_not_sent_again(sleeps):
    session, calls = session_answering(ABORTED, "ok")
    with pytest.raises(requests.ConnectionError):
        session.post("/ords/wwv_flow.accept?p_context=x", data={})
    assert calls == ["POST"]

    session, calls = session_answering(requests.ConnectTimeout(), "ok")  # never sent
    session.post("/ords/wwv_flow.accept?p_context=x", data={})
    assert calls == ["POST", "POST"]


def clients(*failures):
    """new_client(attempt) for scrape_type: attempt n fails with failures[n-1]."""
    made = []

    def new_client(attempt):
        made.append(attempt)
        error = failures[attempt - 1] if attempt <= len(failures) else None

        def iter_pages(**_):
            if error:
                raise error
            yield response(ROW)

        return nullcontext(SimpleNamespace(session=SimpleNamespace(archive=None), iter_pages=iter_pages))
    return new_client, made


ARGS = dict(sessions=1, attempts=3, retry_wait=60, page_size=100, workers=1, delay=0,
            max_attempts=3, detail_limit=0)


def test_a_type_gets_a_fresh_session_after_a_network_failure(sleeps, tmp_path):
    new_repo = lambda: AdRepository(connect(tmp_path / "t.db"))
    new_client, made = clients(ABORTED)
    assert scrape_type(lambda _, attempt: new_client(attempt), new_repo, "12", **ARGS) == (1, 0)
    assert (made, sleeps) == ([1, 2], [60])


def test_fresh_sessions_run_out_and_wrong_pages_are_not_retried(sleeps, tmp_path):
    new_repo = lambda: AdRepository(connect(tmp_path / "t.db"))
    new_client, made = clients(*[requests.ReadTimeout()] * 3)
    with pytest.raises(requests.ReadTimeout):
        scrape_type(lambda _, attempt: new_client(attempt), new_repo, "12", **ARGS)
    assert made == [1, 2, 3]

    new_client, made = clients(DetailPageError("blocked"))
    with pytest.raises(DetailPageError):
        scrape_type(lambda _, attempt: new_client(attempt), new_repo, "12", **ARGS)
    assert made == [1]


def test_a_network_error_keeps_the_other_pages_and_costs_no_attempt():
    import threading

    from scraper.pipeline import fetch_details
    from test_parser import page
    from test_storage import listed

    repo = AdRepository(connect(":memory:"))
    links = listed(repo, "1", "2", "3")
    in_flight = threading.Barrier(3, timeout=5)  # "1" drops while "2" and "3" are being fetched

    def get(link):
        code = link.split("p28_code=")[1].split("&")[0]
        in_flight.wait()
        if code == "1":
            raise ABORTED
        return SimpleNamespace(text=page(code))

    client = SimpleNamespace(session=SimpleNamespace(fork=lambda: SimpleNamespace(get=get)))
    with pytest.raises(requests.ConnectionError):
        fetch_details(client, repo, links, workers=3, delay=0, max_attempts=1)
    assert repo.codes_without_detail(1) == ["1"]  # still wanted: not counted against it


def test_links_still_queued_when_a_session_stops_cost_no_attempt():
    import threading

    from scraper.pipeline import DetailFetcher
    from test_parser import page
    from test_storage import listed

    repo = AdRepository(connect(":memory:"))
    links = listed(repo, "1", "2", "3")
    started = threading.Event()

    def get(link):  # "1" is being fetched while "2" and "3" wait for the one worker
        started.set()
        threading.Event().wait(0.2)  # time.sleep doesn't wait in tests (conftest)
        return SimpleNamespace(text=page(link.split("p28_code=")[1].split("&")[0]))

    client = SimpleNamespace(session=SimpleNamespace(fork=lambda: SimpleNamespace(get=get)))
    with pytest.raises(requests.ConnectionError):
        with DetailFetcher(client, repo, workers=1, delay=0, max_attempts=1) as details:
            details.add(links)
            started.wait(5)
            raise ABORTED  # the listing's connection drops: the session stops
    assert repo.given_up_details(1) == []
    assert repo.codes_without_detail(1) == ["2", "3"]  # still wanted by the next run


def test_detail_pages_at_once_halve_while_connections_drop_and_come_back_slowly(monkeypatch):
    from scraper import pipeline

    clock = [100.0]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: clock[0])
    throttle = pipeline.Throttle(12, burst=3, window=30, recover=5)

    throttle.trouble(), throttle.trouble()
    assert throttle.allowed == 12  # a dropped connection or two: normal
    throttle.trouble()
    assert throttle.allowed == 6
    for _ in range(5):  # the same burst cuts only once
        throttle.trouble()
    assert throttle.allowed == 6
    clock[0] += 31
    for _ in range(3):
        throttle.trouble()
    assert throttle.allowed == 3

    for _ in range(9 * 5 + 10):  # pages without trouble: one more per 5, never above 12
        with throttle:
            pass
    assert throttle.allowed == 12 and throttle.busy == 0
