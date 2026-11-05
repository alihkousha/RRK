from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from apex.archive import ResponseArchive
from apex.page import ajax_action, base_href, form_action, read_items
from apex.report import ApexReportClient, ApexReportConfig
from apex.session import ApexSession
from parsing import ListingParseError, fetched_data

from . import open_data

BASE_URL = "https://rrk.ir"

log = logging.getLogger("rrk.client")


class RRSConfigError(RuntimeError):
    """The report settings could not be determined from the page."""


class RRSClient:
    """Client for the RRS Oracle APEX application.

    Usage::

        with RRSClient() as client:
            data = client.fetch_report_json(noe_agahi="12", page_size=100)

    The first fetch walks the same pages as a browser (home, data page,
    search) and configures the report from the grid the search renders.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        verify_ssl: bool = False,
        # The grid query is slow: the browser waited ~40 s for 100 rows.
        timeout: float = 180.0,
        archive_dir: Path | None = None,
        # Off by default: neither was needed once TCP keepalive fixed the
        # hangs (run7).  True: the browser's 4 page-load calls on the search
        # and results pages / person-like pauses taken from the HAR.
        page_load_calls: bool = False,
        human_pacing: bool = False,
        # False: plain sockets without TCP keepalive probes.
        tcp_keepalive: bool = True,
    ) -> None:
        self.page_load_calls = page_load_calls
        self.human_pacing = human_pacing
        self.session = ApexSession(
            base_url=base_url,
            verify_ssl=verify_ssl,
            timeout=timeout,
            archive=ResponseArchive(archive_dir) if archive_dir else None,
            tcp_keepalive=tcp_keepalive,
        )
        self.home: requests.Response | None = None
        self.page: requests.Response | None = None
        self.grid: open_data.GridConfig | None = None
        self._report_client: ApexReportClient | None = None

    # -- setup ---------------------------------------------------------

    def open_session(self) -> requests.Response:
        """Home page, then the data page through the home page's own link
        (it carries the session id and the ``cs`` checksum)."""
        log.info("step 1/4: home page")
        self.home = self.session.get(open_data.HOME_PATH)
        link = open_data.find_data_page_link(self.home.text)
        if link is None:
            raise RRSConfigError("No link to the data page on the home page.")
        self._pause("home")
        log.info("step 2/4: data page (session %s)", self.session.state.instance)
        self.page = self.session.get(urljoin(self.home.url, link))
        return self.page

    def search(self, filters: Mapping[str, str]) -> requests.Response:
        """Submit the search form with *filters* (item name -> value)."""
        if self.page is None:
            raise RRSConfigError("Call open_session() first.")
        html = self.page.text
        action = form_action(html)
        if action is None:
            raise RRSConfigError("No page form on the data page.")

        if self.page_load_calls:  # the browser runs them before the user can search
            self.run_page_load_calls()

        items = read_items(html)
        for name, value in filters.items():
            items[name] = {"n": name, "v": value}

        self._pause("search")
        log.info("step 3/4: search (request=%s, filters=%s, %d items)",
                 open_data.SEARCH_REQUEST, dict(filters), len(items))
        self.page = self.session.submit_page(
            self._resolve(html, action),
            request=open_data.SEARCH_REQUEST,
            items=list(items.values()),
        )
        return self.page

    def fetch_ad_types(self) -> list[tuple[str, str]]:
        """The ad-type dropdown's choices as ``(code, label)``."""
        return self.fetch_choices(open_data.AD_TYPE_ITEM)

    def fetch_choices(self, item: str) -> list[tuple[str, str]]:
        """A popup dropdown's choices as ``(code, label)``, fetched the way the
        browser does when it opens (100 a page, as it scrolls)."""
        if self.page is None:
            self.open_session()
        html = self.page.text
        identifier = open_data.find_popup_lov_identifier(html, item)
        if identifier is None:
            raise RRSConfigError(f"No {item} dropdown on the page.")
        state = self.session.state
        choices: list[tuple[str, str]] = []
        while True:
            fetch = {"search": "", "sortColumn": None, "sortDirection": None, "version": 1,
                     "firstRow": len(choices) + 1, "maxRows": 100}
            response = self.session.post(self._resolve(html, ajax_action(form_action(html) or "")), data={
                "p_flow_id": state.flow_id,
                "p_flow_step_id": state.flow_step_id,
                "p_instance": state.instance,
                "p_debug": "",
                "p_json": json.dumps({"regions": [{"id": item, "ajaxIdentifier": identifier,
                                                   "fetchData": fetch}],
                                      "salt": state.salt}, ensure_ascii=False, separators=(",", ":")),
            })
            data = fetched_data(response.json())
            choices += [(str(row[1]), row[0]) for row in data["values"]]
            if not data.get("moreData") or not data["values"]:
                return choices

    def connect(self, noe_agahi: str = "12") -> "RRSClient":
        """Open the session, search, and configure the report (idempotent)."""
        if self._report_client is not None:
            return self

        self.open_session()
        self.search({"P199_NOE_AGAHI": noe_agahi})
        html = self.page.text

        self.grid = open_data.parse_grid_config(html)
        if self.grid is None:
            raise RRSConfigError(
                "The search did not render the results grid. Inspect "
                "client.page.text (validation error or session problem?)."
            )

        items = read_items(html)
        self.configure_report(
            ApexReportConfig(
                report_id=self.grid.report_id,
                region_id=self.grid.region_id,
                ajax_identifier=self.grid.ajax_identifier,
                ajax_columns=self.grid.ajax_columns,
                # Like the browser: page order, and listed items the page
                # doesn't render (``P199_FILTER``) are left out.
                page_items=[
                    {"n": name, "v": item["v"]}
                    for name, item in items.items()
                    if name in self.grid.item_names
                ],
            ),
            ajax_path=self._resolve(html, ajax_action(form_action(html) or "")),
        )
        log.info("step 4/4: grid found: region=%s report=%s (%s), %d columns, %d items",
                 self.grid.region_id, self.grid.report_id,
                 open_data.decode_identifier(self.grid.ajax_identifier),
                 len(self.grid.column_names), len(self._report_client.config.page_items))
        if self.page_load_calls:
            self.run_page_load_calls()
        return self

    def run_page_load_calls(self) -> None:
        """The current page's page-load AJAX calls, as the browser makes them."""
        html = self.page.text
        ajax_path = self._resolve(html, ajax_action(form_action(html) or ""))
        identifiers = {i["label"]: i["identifier"] for i in open_data.list_ajax_identifiers(html)}
        items = read_items(html)
        state = self.session.state
        for da_id, names in open_data.PAGE_LOAD_DAS:
            identifier = identifiers.get(f"DA TYPE~~{da_id}")
            if identifier is None:
                log.warning("page-load call %s skipped: not on this page", da_id)
                continue
            page_items = None if names is None else {
                "itemsToSubmit": [{"n": n, "v": items.get(n, {}).get("v", "")} for n in names],
                "protected": state.page_items_protected or "",
                "rowVersion": state.page_items_row_version or "",
                "formRegionChecksums": [],
            }
            started = time.monotonic()
            self.session.post(ajax_path, data={
                "p_flow_id": state.flow_id,
                "p_flow_step_id": state.flow_step_id,
                "p_instance": state.instance,
                "p_debug": "",
                "p_request": "PLUGIN=" + identifier,
                "p_json": json.dumps({"pageItems": page_items, "salt": state.salt},
                                     ensure_ascii=False, separators=(",", ":")),
            })
            log.info("page-load call %s answered in %.1fs", da_id, time.monotonic() - started)

    def _pause(self, moment: str) -> None:
        """Wait about as long as the person in the browser HAR did (±20%)."""
        if not self.human_pacing:
            return
        seconds = open_data.HUMAN_PAUSES[moment] * random.uniform(0.8, 1.2)
        log.info("pausing %.1fs on the %s page, like a person", seconds, moment)
        time.sleep(seconds)

    def _resolve(self, html: str, url: str) -> str:
        """Resolve *url* the way the browser does: against ``<base href>``."""
        return urljoin(urljoin(self.page.url, base_href(html)), url)

    def configure_report(self, config: ApexReportConfig, *, ajax_path: str) -> None:
        """Manual override: configure the report from explicit settings."""
        self._report_client = ApexReportClient(
            session=self.session,
            config=config,
            ajax_path=ajax_path,
        )

    # -- fetching ------------------------------------------------------

    def fetch_report(
        self,
        *,
        noe_agahi: str = "12",
        first_row: int = 1,
        page_size: int = 100,
        filters: dict[str, str] | None = None,
    ) -> str:
        """Fetch the raw report response for an ad type and row range,
        with *filters* (item name -> value) set for this grid query."""
        self.connect(noe_agahi)
        assert self._report_client is not None
        return self._report_client.fetch_raw(
            noe_agahi=noe_agahi,
            first_row=first_row,
            page_size=page_size,
            filters=filters,
        )

    def build_report_form(self, **kwargs: Any) -> dict[str, str]:
        """The form fields that would be POSTed, without sending anything."""
        self.connect(kwargs.get("noe_agahi") or "12")
        assert self._report_client is not None
        return self._report_client.build_form(**kwargs)

    def fetch_report_json(self, **kwargs: Any) -> dict[str, Any]:
        """Like :meth:`fetch_report`, but parsed; raises if not JSON."""
        text = self.fetch_report(**kwargs)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ListingParseError(
                f"Response is not JSON (session expired or request rejected?): "
                f"{text[:300]!r}"
            ) from exc

    def iter_pages(
        self,
        *,
        noe_agahi: str = "12",
        page_size: int = 100,
        delay: float = 2.0,
        filters: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every grid response for *noe_agahi* (and *filters*),
        *page_size* rows each, pausing *delay* seconds between requests.
        Stops when the server reports ``moreData: false`` or returns no rows."""
        first_row = 1
        while True:
            log.info("fetching rows %d-%d (type %s%s)", first_row, first_row + page_size - 1,
                     noe_agahi, f", {filters}" if filters else "")
            response = self.fetch_report_json(
                noe_agahi=noe_agahi, first_row=first_row, page_size=page_size, filters=filters
            )
            yield response

            data = fetched_data(response)
            rows = len(data["values"])
            if not data.get("moreData") or rows == 0:
                return
            first_row += rows
            log.info("got %d rows of %s; pausing %.1fs", rows, data.get("totalRows", "?"), delay)
            time.sleep(delay)

    # -- lifecycle -----------------------------------------------------

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "RRSClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
