from __future__ import annotations

import json
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection

from .archive import ResponseArchive
from .state import ApexState

log = logging.getLogger("rrk.http")

# Headers copied from a real Firefox request to rrk.ir (DevTools, Oct 2026).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0"
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    # shortcut: zstd is advertised but not decodable here (no `zstandard`); rrk.ir only ever answers gzip
    "Accept-Encoding": "gzip, deflate, br, zstd",
}
# Loading a page (GET): what the browser sends when following a link.
NAVIGATION_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}
# APEX's own XHRs (page submit, grid fetch): copied from the grid request.
XHR_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# Something on the path to rrk.ir silently drops a TCP connection after
# ~30 s without traffic (tested: a reused idle connection never answers).
# That kills both a reused connection after a pause and the grid request
# while the server spends 40+ s on the query.  Keepalive probes every 5 s
# keep the connection alive.
KEEPALIVE_OPTIONS = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)] + [
    (socket.IPPROTO_TCP, getattr(socket, name), value)
    for name, value in (("TCP_KEEPIDLE", 5), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 6))
    if hasattr(socket, name)  # Linux names; other systems keep their defaults
]


# Network failures seen in real runs: ConnectTimeout (VPN/network down),
# ReadTimeout, and RemoteDisconnected (a kept-alive connection closed under
# us; the next try opens a new one).  Each is retried after these waits
# (seconds).  An unreachable host fails within CONNECT_TIMEOUT instead of
# the OS's ~135 s.
RETRY_WAITS = (2.0, 15.0, 60.0)
CONNECT_TIMEOUT = 15.0


def _retryable(method: str, url: str, exc: Exception) -> bool:
    """A page submit the server may have received is never sent twice."""
    return isinstance(exc, requests.ConnectTimeout) or not (
        method == "POST" and "wwv_flow.accept" in url)


class _KeepAliveAdapter(HTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["socket_options"] = HTTPConnection.default_socket_options + KEEPALIVE_OPTIONS
        super().init_poolmanager(*args, **kwargs)


@dataclass(slots=True)
class ApexSession:
    """Thin HTTP session wrapper for the RRS Oracle APEX application.

    Behaves like one browser tab: keeps cookies, sends browser headers and a
    ``Referer`` that follows the server's ``Referrer-Policy``, and feeds
    every response into :attr:`state`.
    """

    base_url: str
    verify_ssl: bool = False
    timeout: float = 30.0
    state: ApexState = field(default_factory=ApexState)
    referer: str | None = None
    # When set, every response is saved there (see apex/archive.py).
    archive: ResponseArchive | None = None
    # False: plain sockets, without the keepalive probes (see KEEPALIVE_OPTIONS).
    tcp_keepalive: bool = True
    retry_waits: tuple[float, ...] = RETRY_WAITS
    # Called before each retry: the connection to rrk.ir dropped or timed out.
    on_retry: Callable[[], None] | None = None
    _http: requests.Session = field(
        default_factory=requests.Session,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._http.headers.update(BROWSER_HEADERS)
        if self.tcp_keepalive:
            self._http.mount("https://", _KeepAliveAdapter())
            self._http.mount("http://", _KeepAliveAdapter())

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        headers = dict(NAVIGATION_HEADERS)
        if self.referer is None:  # first page: typed into the address bar
            headers["Sec-Fetch-Site"] = "none"
        headers.update(kwargs.pop("headers", None) or {})
        return self._request("GET", path, headers=headers, **kwargs)

    def post(
        self,
        path: str,
        *,
        data: Any = None,
        json: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        headers = {**XHR_HEADERS, "Origin": self._origin()}
        headers.update(kwargs.pop("headers", None) or {})
        return self._request("POST", path, data=data, json=json, headers=headers, **kwargs)

    def submit_page(
        self,
        action: str,
        *,
        request: str,
        items: list[dict[str, str]],
    ) -> requests.Response:
        """Submit the current page like ``apex.submit({request: ...})``.

        *action* is the form's ``wwv_flow.accept`` URL and *items* the page
        items to send (see :func:`apex.page.read_items`).  APEX answers
        either with the next page or, for "reload on submit: only for
        success", with ``{"redirectURL": ...}``, which is followed here.
        """
        state = self.state
        payload = {
            "pageItems": {
                "itemsToSubmit": items,
                "protected": state.page_items_protected or "",
                "rowVersion": state.page_items_row_version or "",
                "formRegionChecksums": [],
            },
            "salt": state.salt or "",
        }
        response = self.post(
            action,
            data={
                "p_flow_id": state.flow_id or "",
                "p_flow_step_id": state.flow_step_id or "",
                "p_instance": state.instance or "",
                "p_debug": "",
                "p_request": request,
                "p_reload_on_submit": state.reload_on_submit or "S",
                "p_page_submission_id": state.page_submission_id or "",
                "p_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),  # as JSON.stringify
            },
        )
        if "json" in response.headers.get("Content-Type", "").lower():
            redirect = response.json().get("redirectURL")
            if redirect:
                return self.get(urljoin(response.url, redirect))
        return response

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = kwargs.pop("headers", None) or {}
        if self.referer:
            headers.setdefault("Referer", self.referer)
        url = urljoin(self.base_url, path)

        debug = log.isEnabledFor(logging.DEBUG)
        if debug:
            _log_request(method, url, headers, kwargs.get("data"), self)
            log.debug("   waiting up to %.0fs ...", self.timeout)
        for wait in (*self.retry_waits, None):
            started = time.monotonic()
            try:
                response = self._http.request(
                    method,
                    url,
                    headers=headers,
                    timeout=(CONNECT_TIMEOUT, self.timeout),
                    verify=self.verify_ssl,
                    **kwargs,
                )
                break
            except requests.RequestException as exc:
                elapsed = time.monotonic() - started
                log.error("<- %s %s failed after %.1fs: %s", method, _short_url(url), elapsed, exc)
                if self.archive:
                    self.archive.record(method, url, request_headers={**self._http.headers, **headers},
                                        form=kwargs.get("data"), response=None,
                                        elapsed=elapsed, error=exc)
                if wait is None or not isinstance(exc, (requests.ConnectionError, requests.Timeout)) \
                        or not _retryable(method, url, exc):
                    raise
                log.warning("   retrying %s %s in %.0fs", method, _short_url(url), wait)
                if self.on_retry:
                    self.on_retry()
                time.sleep(wait)
        elapsed = time.monotonic() - started
        if debug:
            _log_response(response, elapsed)
        if self.archive:
            saved = self.archive.record(method, url, request_headers={**self._http.headers, **headers},
                                        form=kwargs.get("data"), response=response,
                                        elapsed=elapsed)
            log.debug("   saved %s", saved)

        response.raise_for_status()
        self.state.update_from_url(response.url)
        if "html" in response.headers.get("Content-Type", "").lower():
            self.state.update_from_html(response.text)
            self.referer = self._referer_for(response)
        if debug:
            s = self.state
            log.debug("   state: instance=%s flow=%s/%s salt=%s protected=%s submission_id=%s",
                      s.instance, s.flow_id, s.flow_step_id, _short(s.salt),
                      _short(s.page_items_protected), _short(s.page_submission_id))
        return response

    def _origin(self) -> str:
        parts = urlparse(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def _referer_for(self, response: requests.Response) -> str:
        """What a browser sends as ``Referer`` after this page.  rrk.ir sets
        ``Referrer-Policy: strict-origin``, so that is just ``https://rrk.ir/``."""
        policy = response.headers.get("Referrer-Policy", "").lower()
        if policy in ("origin", "strict-origin"):
            return self._origin() + "/"
        return response.url

    def fork(self) -> "ApexSession":
        """Another tab of the same browser: shares the cookies (and the
        archive), has its own connection, page state and referer."""
        tab = ApexSession(base_url=self.base_url, verify_ssl=self.verify_ssl,
                          timeout=self.timeout, referer=self.referer,
                          archive=self.archive, tcp_keepalive=self.tcp_keepalive,
                          retry_waits=self.retry_waits)
        tab._http.cookies = self._http.cookies
        return tab

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ApexSession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# -- debug logging --------------------------------------------------------


def _short(value: Any, n: int = 16) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= n else f"{text[:n]}...({len(text)})"


def _short_url(url: str) -> str:
    """Readable URL: Persian path decoded, long ``cs`` checksums shortened."""
    head, _, query = unquote(url).partition("?")
    params = [
        f"{k}={_short(v, 12) if k in ('cs', 'dialogCs') else v}"
        for k, _, v in (p.partition("=") for p in query.split("&") if p)
    ]
    return head + ("?" + "&".join(params) if params else "")


def _log_request(method: str, url: str, headers: dict, data: Any, session: "ApexSession") -> None:
    log.debug("-> %s %s", method, _short_url(url))
    cookies = [c.name for c in session._http.cookies]
    log.debug("   cookies: %s", ", ".join(cookies) or "none")
    for name in ("Referer", "Origin", "X-Requested-With"):
        if name in headers:
            log.debug("   %s: %s", name, _short_url(headers[name]))
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if key != "p_json":
            log.debug("   %s = %s", key, _short(value, 40))
    if "p_json" in data:
        _log_p_json(json.loads(data["p_json"]))


def _log_p_json(pj: dict) -> None:
    log.debug("   p_json.salt = %s", _short(pj.get("salt")))
    for region in pj.get("regions", []):
        identifier = region.get("ajaxIdentifier", "")
        log.debug("   p_json.region id=%s report=%s ajaxIdentifier=%s fetchData=%s",
                  region.get("id"), region.get("reportId"), _short(identifier, 24),
                  region.get("fetchData"))
    items = pj.get("pageItems") or {}
    submitted = items.get("itemsToSubmit", [])
    filled = {i["n"]: i["v"] for i in submitted if i.get("v")}
    with_ck = sum(1 for i in submitted if "ck" in i)
    log.debug("   p_json.pageItems: %d items, %d with checksum, filled: %s",
              len(submitted), with_ck, filled or "none")
    log.debug("   p_json.pageItems.protected = %s", _short(items.get("protected")))


def _log_response(response: requests.Response, elapsed: float) -> None:
    for hop in response.history:
        log.debug("   redirect %s -> %s", hop.status_code,
                  _short_url(hop.headers.get("Location", "")))
    content_type = response.headers.get("Content-Type", "")
    log.debug("<- %s %s in %.1fs (%s, %d bytes)", response.status_code,
              _short_url(response.url), elapsed, content_type.split(";")[0],
              len(response.content))
    set_cookies = [c.name for c in response.cookies]
    if set_cookies:
        log.debug("   set-cookie: %s", ", ".join(set_cookies))
    if "json" in content_type:
        log.debug("   body: %s", response.text[:300])
