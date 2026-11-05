"""Static description of the RRS "open data" page (APEX app 200, page 199),
and readers for the parts of it that are only known at runtime.

Browser flow this mirrors:
1. ``GET /`` redirects to the home page, which links to the data page with
   ``?clear=199&session=<id>&cs=<checksum>``.
2. The data page has only the search form; ``apex.submit({request:
   'btnSearch'})`` stores the filters in the session and re-renders it.
3. Only that re-rendered page contains the results grid and its AJAX
   settings (``ajaxIdentifier``, ``ajaxColumns``, ``itemsToSubmit``).
4. The grid loads its rows with a POST to ``wwv_flow.ajax``.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote

# Entry point.  A browser reaches it through two static hops (``/`` sends it
# to ``home.htm`` by JavaScript, ``home.htm`` by meta refresh), neither of
# which sets cookies.  The APEX home page then creates the session and
# redirects to ``home?clear=1&session=<id>``.
HOME_PATH = "/ords/r/rrs/rrs-front/home"

DATA_PAGE_PATH = "/ords/r/rrs/rrs-front/داده-باز"

# Request name of the advanced-search button (the single search box uses
# ``SEARCH``).
SEARCH_REQUEST = "btnSearch"

# The ad-type dropdown (a popup LOV): its choices come from a wwv_flow.ajax
# call the browser makes when the dropdown opens (ad_type_req.har #100).
AD_TYPE_ITEM = "P199_NOE_AGAHI"

# Static id of the results Interactive Grid region.
GRID_STATIC_ID = "AGAHI_GHANOUNI2"
REGION_ID = "1083730177584955508"

# Pauses a person took in the browser HAR (seconds): reading the home page
# before opening the data page, and filling the form before searching.
HUMAN_PAUSES = {"home": 23.0, "search": 7.5}

# Dynamic actions the browser runs when page 199 loads, in its order, with
# the items each submits (None sends ``"pageItems": null``).  ~0.25 s each.
PAGE_LOAD_DAS = (
    ("1601034106907374938", None),  # returns P0_SEARCH_ITEM
    ("912542836717248015", ("P199_FOOTER", "P199_FOOTER_1")),
    ("912495025658247995", ("P199_SINGLE_SEARCH", "P199_SINGLE_SEARCH_1")),
    ("912542395934248015", ("P199_FOOTER", "P199_FOOTER_2")),
)

_DATA_PAGE_SLUG = quote("داده-باز")
_HREF_RE = re.compile(r'href="([^"]*)"')
_IDENTIFIER_RE = re.compile(r'"ajaxIdentifier"\s*:\s*"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True, slots=True)
class GridConfig:
    """AJAX settings of the results grid, as rendered after a search."""

    region_id: str
    report_id: str
    ajax_identifier: str
    ajax_columns: str
    item_names: tuple[str, ...]
    column_names: tuple[str, ...]


def find_data_page_link(home_html: str) -> str | None:
    """The home page's link to the data page, with its ``session`` and ``cs``."""
    for match in _HREF_RE.finditer(home_html):
        href = unescape(match.group(1))
        if (_DATA_PAGE_SLUG in href or "داده-باز" in href) and "cs=" in href:
            return href
    return None


def find_popup_lov_identifier(html: str, item_id: str) -> str | None:
    """The ``ajaxIdentifier`` of the popup LOV *item_id*, from its
    ``apex.widget.popupLov({"itemId": ..., "ajaxIdentifier": ...})`` call."""
    match = re.search(r'apex\.widget\.popupLov\(\{"itemId":"%s","ajaxIdentifier":"((?:[^"\\]|\\.)*)"'
                      % re.escape(item_id), html)
    return _unescape(match.group(1)) if match else None


def parse_grid_config(html: str, static_id: str = GRID_STATIC_ID) -> GridConfig | None:
    """Read the ``interactiveGrid({...})`` call for *static_id*, if rendered."""
    marker = f"apex.jQuery('#{static_id}_ig');ig$.interactiveGrid("
    start = html.find(marker)
    if start < 0:
        return None

    options, _ = json.JSONDecoder().raw_decode(html, start + len(marker))
    config = options["config"]
    current = next(
        (r for r in options.get("savedReports", []) if r.get("isCurrent")),
        None,
    )
    if current is None:
        return None

    return GridConfig(
        region_id=config["regionId"],
        report_id=current["id"],
        ajax_identifier=config["ajaxIdentifier"],
        ajax_columns=config["ajaxColumns"],
        item_names=tuple(
            name.strip().lstrip("#")
            for name in config.get("itemsToSubmit", "").split(",")
            if name.strip()
        ),
        column_names=tuple(c["name"] for c in config["columns"]),
    )


def _unescape(raw: str) -> str:
    """Decode JSON string escapes (``\\u002F`` -> ``/``)."""
    return json.loads(f'"{raw}"')


def decode_identifier(identifier: str) -> str:
    """Readable label of an ajaxIdentifier, e.g. ``'DA TYPE~~912501360951247998'``."""
    head = identifier.split("/", 1)[0]
    try:
        padded = head + "=" * (-len(head) % 4)
        return base64.urlsafe_b64decode(padded).decode("ascii", "replace")
    except Exception:
        return "<undecodable>"


def list_ajax_identifiers(html: str, region_id: str = REGION_ID) -> list[dict]:
    """Every ``ajaxIdentifier`` on the page with its decoded label and its
    distance (in characters) from the nearest occurrence of *region_id*."""
    anchors = [m.start() for m in re.finditer(re.escape(region_id), html)]
    found = []
    for m in _IDENTIFIER_RE.finditer(html):
        identifier = _unescape(m.group(1))
        found.append(
            {
                "position": m.start(),
                "label": decode_identifier(identifier),
                "distance": min((abs(m.start() - a) for a in anchors), default=None),
                "identifier": identifier,
            }
        )
    return found
