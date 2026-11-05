import json
from pathlib import Path

import pytest

from parsing import ListingParseError, detail_links, fetched_data, parse_listing_payload

ROW = [
    "030515436378370", "عنوان", "1403/05/15", "1", "23152",
    "1403/07/02", "1322", "14007342851", "متن آگهی....",
    '<a  href="/ords/r/rrs/rrs-front/f-detail-ad?p28_code=17901614&clear=28" >',
    '<button  href="/ords/r/rrs/rrs-front/f-detail-ad?p28_code=17901614&x=y">جزئیات</button>',
    "انتشار یافته امضا شده", {"v": "1", "d": "معاملاتی"},
    "05/08/2024", "عنوان کامل", {"salt": "x", "protected": "y", "rowVersion": "z"},
]


def response(*rows, more=False, total=None):
    return {"regions": [{"id": "1", "ajaxIdentifier": "x", "fetchedData": {
        "values": list(rows), "firstRow": 1, "moreData": more,
        "totalRows": total if total is not None else len(rows),
    }}]}


def test_parse_listing_rows():
    ad = parse_listing_payload(response(ROW))[0]
    assert ad.tracking_code == "030515436378370"
    assert ad.page_number == "1"
    assert ad.newspaper_number == "23152"
    assert ad.company_national_id == "14007342851"
    assert ad.summary == "متن آگهی...."
    assert ad.detail_code == "17901614"
    assert ad.newspaper_type_code == "1"
    assert ad.newspaper_type == "معاملاتی"
    assert ad.pay_date == "05/08/2024"
    assert ad.full_title == "عنوان کامل"


def test_detail_links_prefer_the_button_and_unescape():
    row = list(ROW)
    row[10] = '<button href="/ords/f-detail-ad?p28_code=17901614&amp;session=1&amp;cs=abc">'
    assert detail_links(response(row)) == {"17901614": "/ords/f-detail-ad?p28_code=17901614&session=1&cs=abc"}


def test_server_error_and_wrong_shape_raise():
    with pytest.raises(ListingParseError, match="session has ended"):
        parse_listing_payload({"error": "Your session has ended."})
    with pytest.raises(ListingParseError):
        parse_listing_payload({"fetchedData": {"values": [ROW]}})
    with pytest.raises(ListingParseError, match="row 0"):
        parse_listing_payload(response(ROW[:10]))


HAR = next((Path(__file__).resolve().parents[1] / "src" / "rrk").glob("*wwv_flow.ajax*.har"), None)


@pytest.mark.skipif(HAR is None, reason="captured browser response not present")
def test_parse_real_response():
    entry = json.loads(HAR.read_text(encoding="utf-8"))["log"]["entries"][0]
    payload = json.loads(entry["response"]["content"]["text"])

    data = fetched_data(payload)
    assert (data["firstRow"], data["moreData"], data["totalRows"]) == (1, True, 554)

    ads = parse_listing_payload(payload)
    assert len(ads) == 100
    assert all(ad.detail_code and ad.tracking_code for ad in ads)
    assert {ad.newspaper_type for ad in ads} <= {"تهران", "شهرستان", "معاملاتی"}
    first = ads[0]
    assert first.tracking_code == "000614657626948"
    assert first.company_national_id == "14007342851"
    assert first.detail_code == "16002615"
