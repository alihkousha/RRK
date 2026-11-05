from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .session import ApexSession


@dataclass(frozen=True, slots=True)
class ApexReportConfig:
    """Static parameters of one APEX report region.

    ``flow_id``, ``flow_step_id`` and ``instance`` are optional: when the
    session has read them from the page (``session.state``) those live values
    are used instead, so nothing session-bound has to be configured by hand.
    """

    report_id: str
    region_id: str
    ajax_identifier: str
    ajax_columns: str
    page_items: list[dict[str, Any]]
    page_checksum: str | None = None
    first_row: int = 1
    max_rows: int = 100
    flow_id: str | None = None
    flow_step_id: str | None = None
    instance: str | None = None


class ApexReportClient:
    """Fetch report rows from the RRS Oracle APEX application."""

    def __init__(
        self,
        session: ApexSession,
        config: ApexReportConfig,
        *,
        ajax_path: str,
    ) -> None:
        self.session = session
        self.config = config
        self.ajax_path = ajax_path

    def build_form(
        self,
        *,
        noe_agahi: str | None = None,
        first_row: int | None = None,
        page_size: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the form fields of the AJAX request without sending it."""
        config = self.config
        state = self.session.state

        # Live values read from the page win over anything configured.
        instance = state.instance or config.instance
        flow_id = state.flow_id or config.flow_id
        flow_step_id = state.flow_step_id or config.flow_step_id

        missing = [
            name
            for name, value in (
                ("instance", instance),
                ("flow_id", flow_id),
                ("flow_step_id", flow_step_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"APEX session values not available: {', '.join(missing)}. "
                "Open the page with the session first."
            )

        row_start = config.first_row if first_row is None else first_row
        row_count = config.max_rows if page_size is None else page_size

        if row_start < 1:
            raise ValueError("first_row must be at least 1")
        if row_count < 1:
            raise ValueError("page_size must be at least 1")

        region = {
            "reportId": config.report_id,
            "view": "grid",
            "ajaxColumns": config.ajax_columns,
            "id": config.region_id,
            "ajaxIdentifier": config.ajax_identifier,
            "fetchData": {
                "version": 1,
                "firstRow": row_start,
                "maxRows": row_count,
            },
        }

        # Copy the list and its items so per-request filters don't mutate
        # the reusable configuration.
        items = [dict(item) for item in config.page_items]

        # APEX sets these items in session state before running the grid query.
        overrides = dict(filters or {})
        if noe_agahi is not None:
            overrides["P199_NOE_AGAHI"] = noe_agahi
        for name, value in overrides.items():
            for item in items:
                if item.get("n") == name:
                    item["v"] = value
                    break
            else:
                items.append({"n": name, "v": value})

        # Session-bound values read from the same page load as the instance.
        # Key order and the empty fields match what the browser sends.
        page_items: dict[str, Any] = {
            "itemsToSubmit": items,
            "protected": state.page_items_protected or config.page_checksum or "",
            "rowVersion": state.page_items_row_version or "",
            "formRegionChecksums": [],
        }

        payload: dict[str, Any] = {
            "regions": [region],
            "pageItems": page_items,
        }
        if state.salt:
            payload["salt"] = state.salt

        form_data = {
            "p_flow_id": flow_id,
            "p_flow_step_id": flow_step_id,
            "p_instance": instance,
            "p_debug": "",
            "p_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),  # as JSON.stringify
        }

        return form_data

    def fetch_raw(
        self,
        *,
        noe_agahi: str | None = None,
        first_row: int | None = None,
        page_size: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> str:
        form_data = self.build_form(
            noe_agahi=noe_agahi,
            first_row=first_row,
            page_size=page_size,
            filters=filters,
        )

        # ApexSession.post sends the browser's XHR headers.
        response = self.session.post(self.ajax_path, data=form_data)
        return response.text
