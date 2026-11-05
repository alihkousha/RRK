import json
from pathlib import Path

import pytest

from parsing import DetailPageError, parse_detail_page


def page(code, text="متن کامل &amp; آگهی", registration_number="-----"):
    """The parts of a detail page the parser reads."""
    return (f'<input type="hidden" id="P28_CODE" name="P28_CODE" value="{code}">'
            '<input type="hidden" id="P28_COMPANYNAME" name="P28_COMPANYNAME" value="شرکت ">'
            f'<input type="hidden" id="P28_SABTNUMBER" name="P28_SABTNUMBER" value="{registration_number}">'
            '<a-dynamic-content region-id="R319178753005813243" ajax-identifier="x">\n'
            f'{text}\n</a-dynamic-content>')


def test_parse_detail_page():
    detail = parse_detail_page(page("7"), "7")
    assert detail.full_text == "متن کامل & آگهی"
    assert detail.company_name == "شرکت"
    assert detail.registration_number is None  # "-----"
    assert detail.issuer is None  # not on the page


def test_other_pages_raise():
    with pytest.raises(DetailPageError, match="not its detail page"):
        parse_detail_page("<html>Web Page Blocked</html>", "7")
    with pytest.raises(DetailPageError, match="not its detail page"):
        parse_detail_page(page("8"), "7")
    with pytest.raises(DetailPageError, match="no ad text"):
        parse_detail_page(page("7").split("<a-dynamic")[0], "7")


HAR = Path(__file__).resolve().parents[1] / "src" / "rrk" / "sample_add_full_detail.har"


@pytest.mark.skipif(not HAR.exists(), reason="captured browser response not present")
def test_parse_real_detail_page():
    entries = json.loads(HAR.read_text(encoding="utf-8"))["log"]["entries"]
    html = next(e["response"]["content"]["text"] for e in entries
                if "/f-detail-ad?" in e["request"]["url"])

    detail = parse_detail_page(html, "15294024")
    assert detail.full_text.startswith("احتراما به عموم بستانكاران شركت ورشكسته زرساخت")
    assert detail.full_text.endswith("دادرس علي البدل اداره تصفيه امورورشكستگي تهران")
    assert detail.company_name == "شهرداری مشهد منطقه یک"
    assert detail.registration_number is None
    assert detail.publication_count == "2"
    assert detail.issuer == "اداره کل تصفیه امور ورشکستگی قوه قضائیه"
