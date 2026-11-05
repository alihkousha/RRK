"""Keep every server response on disk, exactly as received, for later checks.

Each response body is written byte-for-byte to ``NN-METHOD-<page>.<ext>``,
and one line per request is appended to ``index.jsonl``: when it was made,
the URL, the form that was sent, status, timing, the server's response
headers and the SHA-256 of the body.  Together they show which request
produced which data, so a scraped row can be traced back to the server
response that contained it.  Cookie values are never written.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

_SKIP_HEADERS = {"cookie", "set-cookie"}


class ResponseArchive:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._count = 0
        self.last_file: Path | None = None
        self._lock = threading.Lock()  # detail workers share one archive

    def record(self, *args: Any, **kwargs: Any) -> Path | None:
        with self._lock:
            return self._record(*args, **kwargs)

    def _record(
        self,
        method: str,
        url: str,
        *,
        request_headers: dict[str, str],
        form: Any,
        response: requests.Response | None,
        elapsed: float,
        error: Exception | None = None,
    ) -> Path | None:
        """Save one exchange; returns the body file, if there was a body."""
        self._count += 1
        entry: dict[str, Any] = {
            "n": self._count,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "method": method,
            "url": url,
            "request_headers": _without_cookies(request_headers),
            "form": form if isinstance(form, dict) else None,
            "elapsed_s": round(elapsed, 2),
        }

        body_file = None
        if response is not None:
            body = response.content
            content_type = response.headers.get("Content-Type", "")
            body_file = self.directory / (
                f"{self._count:02d}-{method}-{_slug(response.url)}{_extension(content_type)}"
            )
            body_file.write_bytes(body)
            entry.update(
                final_url=response.url,
                redirects=[
                    {"status": r.status_code, "location": r.headers.get("Location")}
                    for r in response.history
                ],
                status=response.status_code,
                response_headers=_without_cookies(dict(response.headers)),
                bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                file=body_file.name,
            )
        if error is not None:
            entry["error"] = f"{type(error).__name__}: {error}"

        self.last_file = body_file
        with (self.directory / "index.jsonl").open("a", encoding="utf-8") as index:
            index.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return body_file


def _without_cookies(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _SKIP_HEADERS}


def _slug(url: str) -> str:
    """``.../rrs-front/داده-باز?x`` -> ``داده-باز``; ``wwv_flow.ajax`` stays readable."""
    name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]) or "root"
    return re.sub(r"[^\w.-]+", "_", name)[:40]


def _extension(content_type: str) -> str:
    content_type = content_type.lower()
    if "html" in content_type:
        return ".html"
    if "json" in content_type:
        return ".json"
    return ".txt"
