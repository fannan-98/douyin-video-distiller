#!/usr/bin/env python3
"""Robust HTTP / media helpers for the Douyin signed downloader.

Borrowed ideas (2026-11-08, see references/douyin-download.md §7):

* Ordered, named failure rules ..... Evil0ctal/Douyin_TikTok_Download_API
                                    src/dtk/transport/classify.py
  A broken signer must be reported AS A BROKEN SIGNER, not as a burnt identity
  or a generic "download failed".
* Fail-safe when signing goes stale .. same repo, src/dtk/signing/protection.py
  The error names the exact constant to refresh (VERSION_CODE / SALT).
* Retry with exponential backoff .... yt-dlp yt_dlp/utils/_utils.py
                                    RetryManager.report_retry
* Range / Content-Length awareness .. yt-dlp yt_dlp/downloader/http.py

This machine's registry proxy breaks urllib TLS, so everything goes over
http.client directly.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
RISK_CODES = frozenset({403, 412, 429})
SIGNATURE_MARKERS = ("Uifid", "uifid", "Signature", "signature", "Argus", "secsdk")
ERROR_PAGE_PREFIXES = (b"<!doctype", b"<!DOCTYPE", b"<html", b"<HTML", b"<?xml")


@dataclass
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    url: str
    redirects: int = 0
    attempts: int = 1

    def header(self, name: str, default: str = "") -> str:
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return default


def _one_pass(url: str, headers: dict[str, str], method: str,
              timeout: int, max_redirects: int) -> Response:
    current = url
    hops = 0
    for _ in range(max_redirects + 1):
        parts = urlsplit(current)
        path = parts.path + (("?" + parts.query) if parts.query else "")
        conn = (http.client.HTTPSConnection(parts.netloc, timeout=timeout)
                if parts.scheme == "https"
                else http.client.HTTPConnection(parts.netloc, timeout=timeout))
        try:
            conn.request(method, path, headers=headers)
            resp = conn.getresponse()
            body = resp.read() if method != "HEAD" else b""
            hdrs = resp.getheaders()
            status = resp.status
        finally:
            conn.close()
        location = ""
        for key, value in hdrs:
            if key.lower() == "location":
                location = value
                break
        if status in REDIRECT_CODES and location and hops < max_redirects:
            current = urljoin(current, location)
            hops += 1
            # 303 must not replay a POST; all our requests are GET/HEAD.
            if status == 303 and method not in ("GET", "HEAD"):
                method = "GET"
            continue
        return Response(status, hdrs, body, current, redirects=hops)
    return Response(status, hdrs, body, current, redirects=hops)


def request(url: str, headers: dict[str, str], *, method: str = "GET",
            timeout: int = 25, max_redirects: int = 5,
            retries: int = 2, backoff: float = 0.8) -> Response:
    """GET/HEAD with redirects and retry-on-transient only.

    Network errors and 5xx are retried with exponential backoff.  4xx is never
    retried: hammering a risk-control refusal with the same signature only
    burns the visitor identity faster (classify.py's lesson).
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = _one_pass(url, headers, method, timeout, max_redirects)
            result.attempts = attempt + 1
            if result.status < 500:
                return result
            last = RuntimeError(f"HTTP {result.status}")
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            last = exc
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    raise RuntimeError(f"request failed after {retries + 1} attempts: {last}")


def classify(status: int, headers: list[tuple[str, str]], body: bytes,
             *, expect_aweme_detail: bool = True) -> tuple[str, str]:
    """Return (failure_class, hint). 'ok' means a usable payload.

    Ordered like classify.py: a specific signature refusal is checked before a
    generic 403, and an empty-but-valid HTTP 200 is its own loud failure.
    """
    head = body[:4096].decode("utf-8", "replace")

    if status in RISK_CODES and any(m in head for m in SIGNATURE_MARKERS):
        return ("signature_refused",
                "Signing constants are stale. Compare VERSION_CODE / SALT / "
                "SDK_VERSION with Evil0ctal/Douyin_TikTok_Download_API "
                "src/dtk/platforms/douyin/params.py and signing/native/websign.py.")
    if status in RISK_CODES:
        return ("risk_control",
                "Visitor identity refused. Wait before retrying; do NOT loop "
                "with the same cookies.")
    if status >= 500:
        return ("network_server", "Upstream 5xx; retry later with backoff.")
    if status != 200:
        return ("http_error", f"Unexpected HTTP {status}.")
    if not body:
        return ("empty_payload",
                "HTTP 200 with an empty body is Douyin's silent refusal, not a "
                "network success. Check the signed query and visitor jar.")
    if body[:1] in (b"{", b"["):
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return ("bad_json", "Response looked like JSON but did not parse.")
        code = payload.get("status_code")
        if code not in (None, 0):
            return ("business_error",
                    f"status_code={code}, status_msg={payload.get('status_msg')!r}")
        if expect_aweme_detail and not payload.get("aweme_detail"):
            return ("withheld_payload",
                    "Envelope parsed but aweme_detail is missing/empty. Usually "
                    "a signature/visitor mismatch (verifyFp must equal the jar's "
                    "s_v_web_id), not a deleted video.")
    return ("ok", "")


def validate_media(blob: bytes, *, content_type: str = "",
                   declared_length: str = "", min_bytes: int = 100_000) -> dict:
    """Reject HTML/JSON error pages and truncated or non-media payloads.

    A 200 response from a CDN is not proof of a video.  Check the container
    magic bytes before anything is written to disk.
    """
    size = len(blob)
    out = {"ok": False, "size": size, "sha256": "", "format": "", "reason": ""}

    if declared_length and declared_length.isdigit() and int(declared_length) != size:
        out["reason"] = f"incomplete: Content-Length={declared_length} but got {size}"
        return out
    if size < min_bytes:
        out["reason"] = f"too_small: {size} < {min_bytes}"
        return out
    if size < 12:
        out["reason"] = "too_short: cannot contain a recognisable container header"
        return out
    if any(blob.startswith(p) for p in ERROR_PAGE_PREFIXES):
        out["reason"] = "error_page: HTML/XML body returned instead of media"
        return out

    fmt = ""
    if len(blob) >= 12 and blob[4:8] == b"ftyp":
        fmt = "mp4"
    elif blob.startswith(b"ID3") or (blob[0] == 0xFF and (blob[1] & 0xE0) == 0xE0):
        fmt = "mp3"
    elif blob[:4] in (b"OggS", b"fLaC", b"RIFF"):
        fmt = blob[:4].decode("ascii", "replace").lower()
    if not fmt:
        out["reason"] = "unknown_format: no recognised container magic bytes"
        return out
    if content_type and not (content_type.startswith("video/")
                             or content_type.startswith("audio/")
                             or content_type in ("application/octet-stream", "binary/octet-stream")):
        out["reason"] = f"wrong_content_type: {content_type}"
        return out

    out.update({"ok": True, "format": fmt,
                "sha256": hashlib.sha256(blob).hexdigest(), "reason": "ok"})
    return out


def save_secret(path: Path, data: dict) -> dict:
    """Write a secret-bearing file with restrictive permissions, opt-in only.

    On Windows `os.chmod(0o600)` is NOT an ACL change - it only maps the
    read-only attribute, and the file stays at 0o666 inheriting the folder ACL.
    So NTFS needs an explicit icacls call; POSIX gets chmod 0600.
    Returns a report so the caller can surface a failure instead of claiming
    the file was protected.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report: dict = {"path": str(path), "platform": os.name, "hardened": False,
                    "detail": ""}
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            report.update({"hardened": True, "detail": "chmod 0600"})
        except OSError as exc:
            report["detail"] = f"chmod failed: {exc}"
        return report

    user = os.environ.get("USERNAME", "")
    if not user:
        report["detail"] = "USERNAME unavailable; icacls skipped"
        return report
    try:
        import subprocess
        # Decode as bytes: Chinese Windows icacls emits GBK, and text=True
        # would force UTF-8 and raise UnicodeDecodeError in the reader thread.
        proc = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, timeout=15,
        )
        if proc.returncode == 0:
            report.update({"hardened": True,
                           "detail": f"icacls: inheritance removed, granted {user} only"})
        else:
            err = (proc.stderr or b"").decode("gbk", "replace").strip()[:200]
            report["detail"] = f"icacls rc={proc.returncode}: {err}"
    except Exception as exc:  # noqa: BLE001
        report["detail"] = f"icacls unavailable: {exc}"
    return report


def merge_status(path: Path, **fields) -> dict:
    """Single authoritative status file for the whole pipeline.

    Stage 0 writes it, later stages MERGE into it.  Two competing status.json
    files used to leave resume-from-checkpoint ambiguous.
    """
    state: dict = {}
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8")) or {}
        except (ValueError, OSError):
            state = {}
    state.update(fields)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state
