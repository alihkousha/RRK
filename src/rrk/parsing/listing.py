from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any

from models.advertisement import Advertisement, extract_detail_code

_DETAIL_HREF_RE = re.compile(r'href="([^"]*[?&]p28_code=[^"]*)"')


class ListingParseError(ValueError):
    pass


def fetched_data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The grid's ``fetchedData`` block of a ``wwv_flow.ajax`` response:
    ``{"values": [...], "firstRow": 1, "moreData": true, "totalRows": 554}``."""
    if "error" in payload:
        raise ListingParseError(f"server returned an error: {payload['error']}")
    try:
        data = payload["regions"][0]["fetchedData"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ListingParseError("response does not contain regions[0].fetchedData") from exc
    if not isinstance(data.get("values"), list):
        raise ListingParseError("fetchedData.values is not a row list")
    return data


def parse_listing_payload(payload: Mapping[str, Any]) -> list[Advertisement]:
    """Parse one ``wwv_flow.ajax`` grid response into advertisement records."""
    records: list[Advertisement] = []
    for index, row in enumerate(fetched_data(payload)["values"]):
        if not isinstance(row, (list, tuple)):
            raise ListingParseError(f"row {index} is not an array")
        try:
            records.append(Advertisement.from_listing_row(row))
        except ValueError as exc:
            raise ListingParseError(f"invalid row {index}: {exc}") from exc
    return records


def detail_links(payload: Mapping[str, Any]) -> dict[str, str]:
    """``detail_code -> link`` from the DETAIL2/DETAIL cells.  The links carry
    the session id and a ``cs`` checksum: they only work in this session."""
    links: dict[str, str] = {}
    for row in fetched_data(payload)["values"]:
        for cell in (row[10], row[9]):
            match = isinstance(cell, str) and _DETAIL_HREF_RE.search(cell)
            if match:
                link = html.unescape(match.group(1))
                links[extract_detail_code(link)] = link
                break
    return links
