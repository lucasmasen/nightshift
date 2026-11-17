"""A small HarnessRouter client (standard library only), following the official API reference
(github.com/HarnessRouter/plugins, skills/harnessrouter/references/api-reference.md, UHP-Version 2026-09-28).

The key is only ever sent to api.harnessrouter.ai and is never printed.
"""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import config

TERMINAL = {"completed", "incomplete", "failed", "cancelled"}
RETRYABLE = {429, 502, 503, 504, 599}  # 599 = network error (no answer)
API_HOST = urlsplit(config.API_BASE).hostname


class HRError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"HarnessRouter {status} {code}: {message}")
        self.status, self.code = status, code


# transport(method, url, headers, body) -> (status, headers, body bytes). Swappable so tests never touch the network.
Transport = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


class _NoKeyOnRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never carry the HarnessRouter key to a different host (e.g. a storage service)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlsplit(newurl).hostname != API_HOST:
            for name in list(new.headers):
                if name.lower() == "authorization":
                    del new.headers[name]
            new.unredirected_hdrs.pop("Authorization", None)
        return new


_OPENER = urllib.request.build_opener(_NoKeyOnRedirect)


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=120) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return 599, {}, json.dumps({"error": {"code": "network_error", "message": str(e)[:200]}}).encode()


def _retry_after(headers: dict, default: float) -> float:
    try:
        return max(0.0, min(60.0, float(headers.get("Retry-After", default))))
    except (TypeError, ValueError):
        return default


@dataclass
class Client:
    key: str
    base: str = config.API_BASE
    transport: Transport = urllib_transport
    sleep: Callable[[float], None] = time.sleep

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.key}", "UHP-Version": config.UHP_VERSION}
        h.update(extra or {})
        return h

    def request(self, method: str, path: str, body: dict | None = None, *, raw: bytes | None = None,
                headers: dict | None = None, retry: bool | None = None, as_bytes: bool = False) -> Any:
        url = path if path.startswith("http") else self.base + path
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != API_HOST or parts.username or parts.password:
            raise ValueError("Refusing to send the HarnessRouter key to another host: " + url)
        data = raw
        h = self._headers(headers)
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        retry = (method == "GET") if retry is None else retry
        status, rh, out = 0, {}, b""
        for attempt in range(5):
            status, rh, out = self.transport(method, url, h, data)
            if status in RETRYABLE and retry and attempt < 4:
                self.sleep(_retry_after(rh, 2.0 ** attempt))
                continue
            break
        if status >= 400:
            try:
                err = json.loads(out).get("error") or {}
            except (ValueError, AttributeError):
                err = {}
            raise HRError(status, err.get("code", "unknown"), err.get("message", out[:200].decode("utf-8", "replace")))
        if as_bytes:
            return out
        ctype = {k.lower(): v for k, v in rh.items()}.get("content-type", "")
        return json.loads(out) if "json" in ctype else out

    # ---- workspace ---------------------------------------------------------------------------------------------
    def me(self) -> dict:
        return self.request("GET", "/v1/me")

    def pick_model(self, base_id: str | None = None) -> str | None:
        base_id = base_id or config.HARNESS_BASE
        bases = self.request("GET", "/v1/bases").get("bases", [])
        base = next((b for b in bases if b.get("id") == base_id), None)
        if not base:
            raise HRError(422, "unsupported_base", f"{base_id} is not offered to this workspace")
        offered = [m["id"] for m in base.get("models", []) if m.get("available")]
        for want in config.PREFERRED_MODELS:
            if want in offered:
                return want
        return base.get("defaultModel")

    def ensure_harness(self, system_prompt: str, name: str | None = None) -> str:
        """Reuse the Harness with this exact name, else create it. Returns its id."""
        name = name or config.HARNESS_NAME
        for h in self.request("GET", "/v1/harnesses").get("harnesses", []):
            if h.get("name") == name:
                return h["id"]
        body = {"name": name, "base": config.HARNESS_BASE, "system_prompt": system_prompt,
                "max_cost_usd": config.TASK_MAX_COST_USD, "timeout_seconds": config.TASK_TIMEOUT_SECONDS}
        model = self.pick_model()
        if model:
            body["default_model"] = model
        return self.request("POST", "/v1/harnesses", body)["id"]

    # ---- files and tasks ---------------------------------------------------------------------------------------
    def upload(self, path: Path, filename: str | None = None) -> str:
        filename = filename or path.name
        boundary = "nightshift" + uuid.uuid4().hex
        ctype = mimetypes.guess_type(filename)[0] or "text/plain"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nuser_data\r\n'.encode(),
            (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
             f"Content-Type: {ctype}\r\n\r\n").encode() + Path(path).read_bytes() + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return self.request("POST", "/v1/files", raw=b"".join(parts),
                            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})["id"]

    def start(self, harness_id: str, text: str, file_ids: list[str]) -> dict:
        content = [{"type": "input_text", "text": text}] + [{"type": "input_file", "file_id": f} for f in file_ids]
        body = {"input": [{"role": "user", "content": content}], "user": config.USER_ID,
                "metadata": {"harness_id": harness_id}, "background": True}
        # Same key + same body may be retried safely; a fresh key per task. The free plan runs 2 tasks at a time,
        # so a 429 one_at_a_time waits (up to ~5 min) for an earlier task to finish instead of failing the night.
        key = str(uuid.uuid4())
        for attempt in range(20):
            try:
                return self.request("POST", "/v1/responses", body, headers={"Idempotency-Key": key}, retry=True)
            except HRError as e:
                if e.code != "one_at_a_time" or attempt == 19:
                    raise
                self.sleep(15)
        raise AssertionError("unreachable")

    def wait(self, response_id: str, every: float = 10.0, limit: float = config.TASK_TIMEOUT_SECONDS + 120,
             on_tick: Callable[[dict, float], None] | None = None) -> dict:
        waited = 0.0
        while True:
            r = self.request("GET", f"/v1/responses/{response_id}")
            if r.get("status") in TERMINAL:
                return r
            if on_tick:
                on_tick(r, waited)
            if waited >= limit:
                self.request("POST", f"/v1/responses/{response_id}/cancel", {}, retry=False)
                raise TimeoutError(f"HarnessRouter task {response_id} ran past {limit:.0f}s; cancelled it")
            self.sleep(every)
            waited += every

    def model_check(self) -> tuple[bool, str]:
        """Run one tiny task on a probe harness of the configured base, to prove the model key works end to end.
        Costs a fraction of a cent once the key works; a missing key fails before anything runs."""
        name = f"Nightshift key probe ({config.HARNESS_BASE})"
        hid = next((h["id"] for h in self.request("GET", "/v1/harnesses").get("harnesses", []) if h.get("name") == name), None)
        if not hid:
            hid = self.request("POST", "/v1/harnesses", {"name": name, "base": config.HARNESS_BASE,
                                                         "system_prompt": "Reply with the single word ok.",
                                                         "max_cost_usd": 0.05, "max_step": 2})["id"]
        try:
            r = self.request("POST", "/v1/responses", {"input": "say ok", "user": config.USER_ID,
                                                       "metadata": {"harness_id": hid}, "background": True},
                             headers={"Idempotency-Key": str(uuid.uuid4())}, retry=True)
            done = self.wait(r["id"], every=3, limit=240)
        except HRError as e:
            return False, f"{e.code}: {e}"
        if done.get("status") == "completed":
            return True, "model key works"
        err = done.get("error") or done.get("incomplete_details") or {}
        return False, err.get("message", str(err)) if isinstance(err, dict) else str(err)

    def changed_files(self, session_id: str) -> list[dict]:
        return self.request("GET", f"/v1/sessions/{session_id}/files").get("files", [])

    def download(self, url: str) -> bytes:
        return self.request("GET", url, as_bytes=True)  # host is checked in request()


def final_text(response: dict) -> str:
    """The answer is the output_text of the LAST message item (earlier ones are progress notes)."""
    msgs = [o for o in response.get("output", []) if o.get("type") == "message"]
    if not msgs:
        return ""
    return "".join(c.get("text", "") for c in msgs[-1].get("content", []) if c.get("type") == "output_text")
