"""Minimal Voca client for other services. Standard library only: copy this file into your project.

    from voca_client import VocaClient

    voca = VocaClient(api_key="voca_xxxxxxxx_yyyy")
    voca.add_word("leverage", translation="đòn bẩy", context="Banks leverage capital.")
    report = voca.add_words(["hedge", {"word": "yield", "translation": "lợi suất"}], source="quant_reader")
    print(report["summary"])

Docs: docs/external-api/README.md
(Copied unchanged from the Voca repo: voca/docs/external-api/voca_client.py.)
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

DEFAULT_BASE_URL = "https://voca-zeta-five.vercel.app/api/v1"
MAX_BATCH_ITEMS = 100
RETRY_STATUSES = {429, 500, 502, 503, 504}


class VocaError(Exception):
    """Request rejected by Voca (4xx) or still failing after retries (5xx / network)."""

    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(f"[{status} {code}] {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class VocaClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0, max_retries: int = 3):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------ public API

    def add_word(self, word: str, **fields: Any) -> dict:
        """Creates the word or merges new meaning/context/tags into it. Returns the result with `status`."""
        return self._request("POST", "/external/vocabulary", {"word": word, **fields})

    def add_words(self, items: Iterable[str | dict], **defaults: Any) -> dict:
        """Imports any number of words, 100 per request. `defaults` apply to items that do not set them.

        Returns {"summary": {...}, "results": [...]} with `index` relative to the full input.
        """
        items = list(items)
        summary = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0, "total": 0}
        results: list[dict] = []
        for start in range(0, len(items), MAX_BATCH_ITEMS):
            chunk = items[start:start + MAX_BATCH_ITEMS]
            body = self._request("POST", "/external/vocabulary/batch", {"items": chunk, **defaults})
            for result in body["results"]:
                results.append({**result, "index": result["index"] + start})
            for key in summary:
                summary[key] += body["summary"].get(key, 0)
        return {"summary": summary, "results": results}

    def lookup(self, word: str, language: str = "en") -> dict:
        """{"found": false} or {"found": true, "id", "word", "meaning"}."""
        query = urllib.parse.urlencode({"word": word, "language": language})
        return self._request("GET", f"/external/vocabulary/lookup?{query}")

    def collections(self) -> list[dict]:
        """Collections this key can import into (own + shared with editor rights)."""
        return self._request("GET", "/external/collections")["items"]

    # ------------------------------------------------------------ transport

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read() or b"null")
            except urllib.error.HTTPError as err:
                body = _json(err.read())
                error = (body or {}).get("error", {})
                if err.code in RETRY_STATUSES and attempt < self.max_retries:
                    time.sleep(_backoff(attempt, err.headers.get("Retry-After")))
                    continue
                raise VocaError(err.code, error.get("code", "HTTP_ERROR"), error.get("message", err.reason), error.get("details")) from None
            except (urllib.error.URLError, TimeoutError) as err:
                if attempt < self.max_retries:
                    time.sleep(_backoff(attempt, None))
                    continue
                raise VocaError(0, "NETWORK_ERROR", str(err)) from None
        raise AssertionError("unreachable")


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return min(30.0, 2 ** attempt) + random.random()
