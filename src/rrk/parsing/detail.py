from __future__ import annotations

import re
from html import unescape

from apex.page import read_items
from models.advertisement import AdDetail

# The "متن آگهی:" region: the ad's full text, rendered as plain text.
_TEXT_RE = re.compile(
    r'<a-dynamic-content region-id="R319178753005813243"[^>]*>(.*?)</a-dynamic-content>', re.S)


class DetailPageError(ValueError):
    """The server answered with something other than the ad's detail page
    (APEX error page, firewall block page)."""


def parse_detail_page(html: str, code: str) -> AdDetail:
    """The ad's own content from its detail page; raises
    :class:`DetailPageError` for any other page."""
    items = {name: item["v"] for name, item in read_items(html).items()}
    if items.get("P28_CODE") != code:
        title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        raise DetailPageError(f"{code}: not its detail page (P28_CODE={items.get('P28_CODE')!r}, "
                              f"title={title and unescape(title.group(1)).strip()!r}, {len(html)} chars)")
    text = _TEXT_RE.search(html)
    if text is None:
        raise DetailPageError(f"{code}: the page has no ad text region")
    return AdDetail(
        full_text=unescape(text.group(1)).strip() or None,
        company_name=_value(items, "P28_COMPANYNAME"),
        registration_number=_value(items, "P28_SABTNUMBER"),
        publication_count=_value(items, "P28_HCNEWSSTAGE"),
        issuer=_value(items, "P28_AGAHI_SADER_KONANDE"),
    )


def _value(items: dict[str, str], name: str) -> str | None:
    value = items.get(name, "").strip()
    return None if not value.strip("-") else value  # "-----" means none
