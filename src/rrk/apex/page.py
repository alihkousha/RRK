"""Read the submittable state of a rendered APEX page from its HTML."""

from __future__ import annotations

import re
from html import unescape

_INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_FORM_TAG_RE = re.compile(r'<form\b[^>]*\bid="wwvFlowForm"[^>]*>', re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
# Page items are named P<page>_<NAME>; p_flow_id etc. are the form's own fields.
_ITEM_NAME_RE = re.compile(r"^P\d+_\w+$")


def _attrs(tag: str) -> dict[str, str]:
    return {
        name.lower(): unescape(double if double or not single else single)
        for name, double, single in _ATTR_RE.findall(tag)
    }


def read_items(html: str) -> dict[str, dict[str, str]]:
    """Every page item with its current value, in page order.

    Returns ``{name: {"n": name, "v": value}}``, plus ``"ck"`` for items
    protected by a checksum (APEX renders it as ``<input data-for="NAME">``).
    Popup LOVs submit their hidden input, which carries the item's name.
    """
    items: dict[str, dict[str, str]] = {}
    checksums: dict[str, str] = {}

    for tag in _INPUT_TAG_RE.findall(html):
        attrs = _attrs(tag)
        if "data-for" in attrs:
            checksums[attrs["data-for"]] = attrs.get("value", "")
            continue
        name = attrs.get("name", "")
        if not _ITEM_NAME_RE.match(name) or name in items:
            continue
        if attrs.get("type", "").lower() in ("radio", "checkbox") and "checked" not in attrs:
            continue
        items[name] = {"n": name, "v": attrs.get("value", "")}

    for name, ck in checksums.items():
        if name in items:
            items[name]["ck"] = ck
    return items


def form_action(html: str) -> str | None:
    """The page form's action, e.g. ``wwv_flow.accept?p_context=<app>/<page>/<session>``."""
    match = _FORM_TAG_RE.search(html)
    return _attrs(match.group(0)).get("action") if match else None


def base_href(html: str) -> str:
    """The page's ``<base href>`` (APEX renders ``/ords/``), against which
    the form action and AJAX URL resolve; ``""`` if there is none."""
    match = re.search(r"<base\b[^>]*>", html, re.IGNORECASE)
    return _attrs(match.group(0)).get("href", "") if match else ""


def ajax_action(accept_action: str) -> str:
    """The AJAX endpoint for the same page: friendly-URL apps send AJAX calls
    to ``wwv_flow.ajax`` with the same ``p_context`` as the form."""
    return accept_action.replace("wwv_flow.accept", "wwv_flow.ajax", 1)
