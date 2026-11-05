"""Steps 1-3 of the browser flow, replayed against pages saved from rrk.ir.

The saved pages are not committed (they hold a session id), so these tests
skip when they are missing.
"""
import json
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote_plus

import pytest
import requests

from apex.page import ajax_action, form_action, read_items
from rrs import open_data
from rrs.client import RRSClient

PAGES = Path(__file__).resolve().parents[1] / "src" / "rrk"
HOME = PAGES / "روزنامه رسمی کشور.html"
SEARCH = PAGES / "داده باز.html"
RESULTS = PAGES / "داده باز-data.html"

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in (HOME, SEARCH, RESULTS)),
    reason="saved rrk.ir pages not present",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_home_links_to_data_page_with_checksum():
    link = open_data.find_data_page_link(read(HOME))
    assert link.startswith("https://rrk.ir/ords/r/rrs/rrs-front/" + quote("داده-باز"))
    query = parse_qs(link.split("?", 1)[1])
    assert query["clear"] == ["199"]
    assert query["session"] == ["6454780800848"]
    assert query["cs"][0]


def test_search_page_has_no_grid_but_results_page_does():
    assert open_data.parse_grid_config(read(SEARCH)) is None

    grid = open_data.parse_grid_config(read(RESULTS))
    assert grid.region_id == open_data.REGION_ID
    assert grid.report_id == "1115937865841778825"
    assert open_data.decode_identifier(grid.ajax_identifier).startswith("REGION TYPE~~")
    assert grid.ajax_columns
    assert grid.item_names[0] == "P199_COMPANY_NAME"
    assert "P199_FILTER" in grid.item_names
    assert len(grid.column_names) == 15


def test_read_items_values_and_checksums():
    items = read_items(read(RESULTS))
    assert items["P199_NOE_AGAHI"] == {"n": "P199_NOE_AGAHI", "v": "12"}
    assert items["P199_SABTNODATE_AZ"]["v"] == ""  # ``name = "..."`` spacing
    assert items["P199_TYPEPAY"]["v"] == "1"  # only the checked radio
    assert items["P0_CURRENT_PAGE_ID"]["ck"]


def test_ajax_action_keeps_page_context():
    action = form_action(read(SEARCH))
    assert action.startswith("wwv_flow.accept?p_context=rrs-front/")
    assert ajax_action(action).startswith("wwv_flow.ajax?p_context=rrs-front/")


class FakeTransport:
    """Answers the client's requests with the saved pages, in browser order."""

    def __init__(self, total_rows=0):
        self.requests = []
        self.total_rows = total_rows

    def __call__(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if method == "GET" and url == "https://rrk.ir/ords/r/rrs/rrs-front/home":
            return self.html("https://rrk.ir/ords/r/rrs/rrs-front/home?clear=1&session=6454780800848", HOME)
        if method == "GET" and "clear=199" in url:
            return self.html(url, SEARCH)
        if method == "POST" and "wwv_flow.accept" in url:
            return self.html("https://rrk.ir/ords/r/rrs/rrs-front/" + quote("داده-باز"), RESULTS)
        if method == "POST" and "wwv_flow.ajax" in url and "p_request" in kwargs["data"]:
            return self.json(url, {"item": [{"id": "P0_SEARCH_ITEM", "value": ""}]})
        if method == "POST" and "wwv_flow.ajax" in url:
            p_json = json.loads(kwargs["data"]["p_json"])
            if p_json["regions"][0].get("id") == open_data.AD_TYPE_ITEM:
                return self.json(url, {"regions": [{"fetchedData": {
                    "values": [["ابلاغ", "12"], ["تاسيس", "1"]], "firstRow": 1, "moreData": False}}]})
            return self.grid_page(url, p_json)
        raise AssertionError(f"unexpected request {method} {url}")

    def grid_page(self, url, p_json):
        """A grid response of numbered fake rows out of ``total_rows``."""
        fetch = p_json["regions"][0]["fetchData"]
        first, last = fetch["firstRow"], min(fetch["firstRow"] + fetch["maxRows"] - 1, self.total_rows)
        rows = [[str(n)] + [""] * 14 + [{}] for n in range(first, last + 1)]
        body = {"regions": [{"fetchedData": {
            "values": rows, "firstRow": first,
            "moreData": last < self.total_rows, "totalRows": self.total_rows,
        }}]}
        return self.json(url, body)

    @staticmethod
    def json(url, body):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(body).encode()
        return response

    @staticmethod
    def html(url, path):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "text/html; charset=UTF-8"
        response.headers["Referrer-Policy"] = "strict-origin"  # as rrk.ir sends
        response._content = path.read_bytes()
        response.encoding = "utf-8"
        return response


def test_client_walks_home_data_search_and_builds_grid_request():
    transport = FakeTransport()
    client = RRSClient(page_load_calls=True)
    client.session._http.request = transport

    form = client.build_report_form(noe_agahi="12", first_row=1, page_size=5)

    (_, home_url, _), (_, data_url, data_kw), *search_calls, (_, accept_url, accept_kw) = transport.requests[:7]
    results_calls = transport.requests[7:]
    assert len(transport.requests) == 11
    assert "cs=" in data_url

    # The four page-load calls run on both pages, in browser order, each with its page's salt.
    for calls, salt in ((search_calls, "198012610245722289590497485002575217424"),
                        (results_calls, "319686454942007367641623061785582991795")):
        labels = [open_data.decode_identifier(kw["data"]["p_request"][len("PLUGIN="):])
                  for _, _, kw in calls]
        assert labels == ["DA TYPE~~" + da for da, _ in open_data.PAGE_LOAD_DAS]
        assert all(json.loads(kw["data"]["p_json"])["salt"] == salt for _, _, kw in calls)
    assert json.loads(results_calls[0][2]["data"]["p_json"])["pageItems"] is None
    footer = json.loads(results_calls[1][2]["data"]["p_json"])["pageItems"]
    assert footer["itemsToSubmit"] == [{"n": "P199_FOOTER", "v": ""}, {"n": "P199_FOOTER_1", "v": ""}]
    assert list(footer) == ["itemsToSubmit", "protected", "rowVersion", "formRegionChecksums"]
    assert data_kw["headers"]["Referer"] == "https://rrk.ir/"  # Referrer-Policy: strict-origin

    # The search submit echoes the search page's values back.
    accept = accept_kw["data"]
    assert accept_url.startswith("https://rrk.ir/ords/wwv_flow.accept?p_context=")
    assert accept["p_request"] == "btnSearch"
    assert accept["p_instance"] == "6454780800848"
    assert accept["p_page_submission_id"] == "198012610245722289590497485002575217424"
    submitted = json.loads(accept["p_json"])
    assert submitted["salt"] == "198012610245722289590497485002575217424"
    assert "&#x" not in submitted["pageItems"]["protected"]
    by_name = {i["n"]: i for i in submitted["pageItems"]["itemsToSubmit"]}
    assert by_name["P199_NOE_AGAHI"]["v"] == "12"
    assert by_name["P0_ORDER_ID"]["ck"]
    assert accept_kw["headers"]["Origin"] == "https://rrk.ir"

    # The grid request uses the results page's own settings and salt.
    assert client._report_client.ajax_path.startswith(
        "https://rrk.ir/ords/wwv_flow.ajax?p_context="
    )
    assert form["p_flow_step_id"] == "199"
    payload = json.loads(form["p_json"])
    region = payload["regions"][0]
    assert open_data.decode_identifier(region["ajaxIdentifier"]).startswith("REGION TYPE~~")
    assert region["fetchData"] == {"version": 1, "firstRow": 1, "maxRows": 5}
    assert payload["salt"] == "319686454942007367641623061785582991795"
    names = [i["n"] for i in payload["pageItems"]["itemsToSubmit"]]
    assert set(names) == set(client.grid.item_names) - {"P199_FILTER"}


HAR = next(PAGES.glob("*wwv_flow.ajax*.har"), None)


@pytest.mark.skipif(HAR is None, reason="captured browser request not present")
def test_grid_request_matches_browser_capture():
    """Our grid request equals the browser's, apart from per-page-load values."""
    entry = json.loads(HAR.read_text(encoding="utf-8"))["log"]["entries"][0]
    browser_url = entry["request"]["url"]
    browser = {p["name"]: p["value"] for p in entry["request"]["postData"]["params"]}

    client = RRSClient()
    client.session._http.request = FakeTransport()
    ours = client.build_report_form(noe_agahi="12", first_row=1, page_size=100)

    assert client._report_client.ajax_path.split("?")[0] == browser_url.split("?")[0]
    for key in ("p_flow_id", "p_flow_step_id", "p_instance", "p_debug"):
        assert ours[key] == browser[key]
    assert ours.keys() == browser.keys()

    a, b = json.loads(ours["p_json"]), json.loads(unquote_plus(browser["p_json"]))
    # Serialised like the browser's JSON.stringify (no spaces), byte for byte.
    assert ours["p_json"] == json.dumps(a, ensure_ascii=False, separators=(",", ":"))
    for pj in (a, b):
        # Signed anew on every page load: the salt, the protected-items
        # checksum, and the signature at the end of ajaxColumns.
        pj.pop("salt")
        pj["pageItems"].pop("protected")
        pj["regions"][0]["ajaxColumns"] = pj["regions"][0]["ajaxColumns"][:400]
    assert a == b


def test_iter_pages_walks_every_row_once():
    from parsing import parse_listing_payload

    transport = FakeTransport(total_rows=5)
    client = RRSClient()
    client.session._http.request = transport

    pages = list(client.iter_pages(page_size=2, delay=0))

    fetches = [json.loads(kw["data"]["p_json"])["regions"][0]["fetchData"]
               for method, url, kw in transport.requests
               if "wwv_flow.ajax" in url and "p_request" not in kw["data"]]
    assert [f["firstRow"] for f in fetches] == [1, 3, 5]
    assert all(f["maxRows"] == 2 for f in fetches)
    codes = [ad.tracking_code for page in pages for ad in parse_listing_payload(page)]
    assert codes == ["1", "2", "3", "4", "5"]
    # The session is opened and searched only once.
    assert sum("wwv_flow.accept" in url for _, url, _ in transport.requests) == 1


def test_archive_saves_every_response_with_provenance(tmp_path):
    import hashlib

    transport = FakeTransport(total_rows=3)
    client = RRSClient(archive_dir=tmp_path)
    client.session._http.request = transport
    list(client.iter_pages(page_size=2, delay=0))

    index = [json.loads(line) for line in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["method"], e["file"]) for e in index] == [
        ("GET", "01-GET-home.html"),
        ("GET", "02-GET-داده-باز.html"),
        ("POST", "03-POST-داده-باز.html"),  # the fake answers the submit with the page itself
        ("POST", "04-POST-wwv_flow.ajax.json"),
        ("POST", "05-POST-wwv_flow.ajax.json"),
    ]
    for entry in index:
        body = (tmp_path / entry["file"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
        assert entry["bytes"] == len(body)
        assert not {"cookie", "set-cookie"} & {h.lower() for h in entry["request_headers"]}
        assert "User-Agent" in entry["request_headers"]
    assert json.loads(index[3]["form"]["p_json"])["regions"][0]["fetchData"]["firstRow"] == 1
    assert client.session.archive.last_file.name == "05-POST-wwv_flow.ajax.json"


def test_page_load_calls_are_off_by_default():
    transport = FakeTransport()
    client = RRSClient()
    client.session._http.request = transport
    client.build_report_form()

    assert [method for method, _, _ in transport.requests] == ["GET", "GET", "POST"]  # home, data page, submit


def test_human_pacing_pauses_like_the_browser_and_is_off_by_default(sleeps):
    transport = FakeTransport()
    client = RRSClient(human_pacing=True)
    client.session._http.request = transport
    client.build_report_form()

    # Home page before the data page (~23 s), search page before the submit (~7.5 s).
    home, search = sleeps
    assert 23.0 * 0.8 <= home <= 23.0 * 1.2
    assert 7.5 * 0.8 <= search <= 7.5 * 1.2
    sleeps.clear()

    client = RRSClient()
    client.session._http.request = FakeTransport()
    client.build_report_form()
    assert sleeps == []


def test_sockets_get_tcp_keepalive_unless_switched_off():
    import socket

    def socket_options(client):
        return client.session._http.get_adapter("https://rrk.ir/").poolmanager.connection_pool_kw.get(
            "socket_options", [])

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in socket_options(RRSClient())
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) not in socket_options(RRSClient(tcp_keepalive=False))


HAR_SESSION = PAGES / "logs.har"


@pytest.mark.skipif(not HAR_SESSION.exists(), reason="full browser session HAR not present")
def test_page_load_call_matches_browser_capture():
    """Our P199_FOOTER call equals the browser's (HAR #156) apart from per-page-load values."""
    entries = json.loads(HAR_SESSION.read_text(encoding="utf-8"))["log"]["entries"]
    label = "DA TYPE~~" + open_data.PAGE_LOAD_DAS[1][0]
    browser = next(
        {p["name"]: unquote_plus(p["value"]) for p in e["request"]["postData"]["params"]}
        for e in entries if "postData" in e["request"]  # #1 is an aborted POST without a body
        and any(p["name"] == "p_request" and open_data.decode_identifier(
            unquote_plus(p["value"])[len("PLUGIN="):]) == label for p in e["request"]["postData"]["params"]))

    transport = FakeTransport()
    client = RRSClient(page_load_calls=True)
    client.session._http.request = transport
    client.build_report_form()
    ours = transport.requests[-3][2]["data"]

    assert list(ours) == list(browser)
    a, b = json.loads(ours["p_json"]), json.loads(browser["p_json"])
    for pj in (a, b):
        pj.pop("salt")
        pj["pageItems"].pop("protected")
    assert a == b


@pytest.mark.skipif(HAR is None, reason="captured browser request not present")
def test_grid_request_headers_match_browser():
    entry = json.loads(HAR.read_text(encoding="utf-8"))["log"]["entries"][0]
    browser = {h["name"]: h["value"] for h in entry["request"]["headers"]}

    transport = FakeTransport(total_rows=1)
    client = RRSClient()
    client.session._http.request = transport
    client.fetch_report(page_size=1)

    _, _, kwargs = transport.requests[-1]
    ours = {**client.session._http.headers, **kwargs["headers"]}
    # Set by the HTTP layer itself.
    for name in ("Host", "Content-Length", "Connection", "Cookie"):
        browser.pop(name, None)
        ours.pop(name, None)
    assert ours == browser


AD_TYPE_HAR = PAGES / "ad_type_req.har"


def test_ad_types_come_from_the_dropdown_call():
    transport = FakeTransport()
    client = RRSClient()
    client.session._http.request = transport

    assert client.fetch_ad_types() == [("12", "ابلاغ"), ("1", "تاسيس")]
    # home, data page, then the dropdown: no search needed
    assert [m for m, _, _ in transport.requests] == ["GET", "GET", "POST"]


@pytest.mark.skipif(not AD_TYPE_HAR.exists(), reason="captured browser request not present")
def test_ad_type_request_matches_browser_capture():
    """Equal to the browser's dropdown call (ad_type_req.har #100) apart from per-page-load values."""
    entry = json.loads(AD_TYPE_HAR.read_text(encoding="utf-8"))["log"]["entries"][100]
    browser = {p["name"]: unquote_plus(p["value"]) for p in entry["request"]["postData"]["params"]}

    transport = FakeTransport()
    client = RRSClient()
    client.session._http.request = transport
    client.fetch_ad_types()
    ours = transport.requests[-1][2]["data"]

    assert list(ours) == list(browser)
    a, b = json.loads(ours["p_json"]), json.loads(browser["p_json"])
    for pj in (a, b):
        pj.pop("salt")
        assert open_data.decode_identifier(pj["regions"][0].pop("ajaxIdentifier")) == "ITEM TYPE~5120~1471546656130409378"
    assert a == b
