from rrs.client import RRSClient
from test_listing import ROW, response


def paged(client, rows, more_after_last=False):
    """Serve *rows* through the client's grid call, recording each call's arguments."""
    calls = []

    def fetch_report_json(*, noe_agahi, first_row, page_size, filters):
        calls.append((noe_agahi, first_row, page_size, filters))
        page = rows[first_row - 1:first_row - 1 + page_size]
        more = first_row - 1 + page_size < len(rows) or more_after_last
        return response(*page, more=more, total=len(rows))

    client.fetch_report_json = fetch_report_json
    return calls


def test_pages_follow_on_from_the_last_row_with_the_same_filters_and_pauses(sleeps):
    client = RRSClient()
    calls = paged(client, [ROW] * 250)
    filters = {"P199_SABTNODATE_AZ": "1403/01/01", "P199_SABTNODATE_TA": "1404/01/01"}

    pages = list(client.iter_pages(noe_agahi="4", page_size=100, filters=filters))

    # Page 2 is the same request from row 101 (second_batch.har #175); no page after moreData: false.
    assert [len(p["regions"][0]["fetchedData"]["values"]) for p in pages] == [100, 100, 50]
    assert calls == [("4", 1, 100, filters), ("4", 101, 100, filters), ("4", 201, 100, filters)]
    assert sleeps == [2.0, 2.0]


def test_an_empty_page_ends_the_listing_even_if_the_server_says_more():
    client = RRSClient()
    calls = paged(client, [ROW] * 100, more_after_last=True)

    assert len(list(client.iter_pages(page_size=100))) == 2
    assert [first for _, first, _, _ in calls] == [1, 101]
