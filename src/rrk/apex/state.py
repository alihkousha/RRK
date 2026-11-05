from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, urlparse

_INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')


@dataclass(slots=True)
class ApexState:
    """Session-specific identifiers that Oracle APEX embeds in its URLs.

    APEX ties each browser session to an ``instance`` id (``p_instance`` in
    AJAX requests, ``session`` in page URLs).  It changes on every visit, so
    it must be read from the server's responses instead of being hardcoded.
    Values are only overwritten when a URL actually carries them.
    """

    instance: str | None = None
    flow_id: str | None = None
    flow_step_id: str | None = None
    # Session-bound values APEX renders into every page and echoes back in
    # each AJAX call (``p_json.salt`` and ``p_json.pageItems.protected``).
    salt: str | None = None
    page_items_protected: str | None = None
    page_items_row_version: str | None = None
    # Per page load; a page submit must echo them back.
    page_submission_id: str | None = None
    reload_on_submit: str | None = None

    # APEX spells these differently depending on the URL style.
    _KEYS = {
        "instance": ("session", "p_instance"),
        "flow_id": ("p_flow_id",),
        "flow_step_id": ("p_flow_step_id",),
    }

    def update_from_url(self, url: str) -> None:
        """Pick up any APEX identifiers present in the query string of *url*."""
        query = parse_qs(urlparse(url).query)
        # Query keys are matched case-insensitively (e.g. ``session``/``SESSION``).
        lowered = {key.lower(): values for key, values in query.items()}

        for attr, names in self._KEYS.items():
            for name in names:
                values = lowered.get(name)
                if values and values[0]:
                    setattr(self, attr, values[0])
                    break

    # Hidden form inputs that APEX renders on every page, keyed by element id.
    _HTML_IDS = {
        "pInstance": "instance",
        "pFlowId": "flow_id",
        "pFlowStepId": "flow_step_id",
        "pSalt": "salt",
        "pPageItemsProtected": "page_items_protected",
        "pPageItemsRowVersion": "page_items_row_version",
        "pPageSubmissionId": "page_submission_id",
        "pReloadOnSubmit": "reload_on_submit",
    }

    def update_from_html(self, html: str) -> None:
        """Pick up identifiers from APEX's hidden ``<input>`` elements.

        Friendly-URL pages carry no ``?session=`` in the address, but every
        page still embeds ``<input type="hidden" id="pInstance" value="...">``.
        """
        for tag in _INPUT_TAG_RE.findall(html):
            attrs = {
                name.lower(): double if double or not single else single
                for name, double, single in _ATTR_RE.findall(tag)
            }
            attr = self._HTML_IDS.get(attrs.get("id", ""))
            # APEX entity-encodes attribute values (``/`` as ``&#x2F;``).
            value = unescape(attrs.get("value") or "")
            if attr and value:
                setattr(self, attr, value)

    @property
    def is_established(self) -> bool:
        """True once an APEX session id has been seen."""
        return self.instance is not None
