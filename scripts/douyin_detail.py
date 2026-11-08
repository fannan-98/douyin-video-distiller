#!/usr/bin/env python3
"""Fetch a Douyin aweme detail payload without yt-dlp.

Why this exists
---------------
yt-dlp's DouyinIE stops at "Fresh cookies needed" because the web detail API
needs a signed request.  This client borrows the open-source pure-Python port of
Douyin's signing stack and issues that request directly:

  * a_bogus / SM3 / browser-info ...... Evil0ctal/Douyin_TikTok_Download_API
                                       src/dtk/signing/native/{abogus,sm3}.py
  * x-secsdk-web-signature ............ same repo, .../native/websign.py
  * query parameter template ......... same repo,
                                       src/dtk/platforms/douyin/params.py
  * s_v_web_id generator ............. Johnserf-Seed/TikTokDownload
                                       Server/s_v_web_id.js
  * redirects / backoff / classification / media magic-byte validation ...
                                       scripts/dy_http.py (see its docstring)

Network note: uses http.client directly, not urllib.  This machine has a
registry system proxy that breaks urllib's TLS handshake; http.client does not
read the registry proxy.

Usage:
    python douyin_detail.py <video_id> --download \
        --out "D:/douyin-distill/<id>/api" \
        --workdir "D:/douyin-distill/<id>"
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dy_http import (  # noqa: E402
    classify,
    merge_status,
    request as dy_request,
    save_secret,
    validate_media,
)
from dy_sign import websign  # noqa: E402
from dy_sign.abogus import ABogus, DEFAULT_BROWSER_INFO  # noqa: E402

VIDEO_ID_RE = re.compile(r"\d{15,25}")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
DETAIL_PATH = "/aweme/v1/web/aweme/detail/"
VERIFY_FP_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
MS_TOKEN_ALPHABET = VERIFY_FP_ALPHABET + "-_"
MS_TOKEN_LENGTH = 126

#: Signing constants that go stale when Douyin ships a new web build.  Kept in
#: one place so classify() can name them in its failure hint.
SIGNING_CONSTANTS = {
    "AID": "6383",
    "VERSION_CODE": "290100",
    "VERSION_NAME": "29.1.0",
    "UPDATE_VERSION_CODE": "170400",
    "WEBSIGN_SALT": websign.SALT,
}


def base36(number: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    while number:
        number, i = divmod(number, 36)
        out.append(alphabet[i])
    return "".join(reversed(out))


def create_s_v_web_id() -> str:
    """Port of TikTokDownload's Server/s_v_web_id.js (UUID-v4-shaped tail)."""
    rng = random.Random()
    tail = [""] * 36
    for index in (8, 13, 18, 23):
        tail[index] = "_"
    tail[14] = "4"
    for index in range(36):
        if tail[index]:
            continue
        position = int(rng.random() * len(VERIFY_FP_ALPHABET))
        if index == 19:
            position = 3 & position | 8
        tail[index] = VERIFY_FP_ALPHABET[position]
    return "verify_" + base36(int(time.time() * 1000)) + "_" + "".join(tail)


def gen_false_ms_token(length: int = MS_TOKEN_LENGTH) -> str:
    rng = random.Random()
    return "".join(rng.choice(MS_TOKEN_ALPHABET) for _ in range(length)) + "=="


def base_params(aweme_id: str) -> dict[str, str]:
    """Mirrors dtk.platforms.douyin.params.base_params()."""
    return {
        "device_platform": "webapp",
        "aid": SIGNING_CONSTANTS["AID"],
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "version_code": SIGNING_CONSTANTS["VERSION_CODE"],
        "version_name": SIGNING_CONSTANTS["VERSION_NAME"],
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "130.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "130.0.0.0",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "12",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "0",
        "update_version_code": SIGNING_CONSTANTS["UPDATE_VERSION_CODE"],
        "msToken": gen_false_ms_token(),
        "aweme_id": aweme_id,
    }


def http_request(url: str, headers: dict[str, str], timeout: int = 25,
                 method: str = "GET"):
    """Compatibility wrapper: redirects + retry are now handled by dy_http."""
    resp = dy_request(url, headers, method=method, timeout=timeout)
    return resp.status, resp.headers, resp.body


def collect_cookies(video_id: str, session_file: Path | None = None
                    ) -> tuple[dict[str, str], list[str]]:
    """Warm up a guest session, reusing a saved jar when one is available.

    Always minting a brand-new visitor makes every run look like a new browser;
    reuse is both faster and less likely to be flagged.
    """
    jar: dict[str, str] = {}
    trace: list[str] = []
    if session_file and session_file.is_file():
        try:
            loaded = json.loads(session_file.read_text(encoding="utf-8"))
            if all(loaded.get(k) for k in ("UIFID_TEMP", "s_v_web_id", "ttwid")):
                jar = {str(k): str(v) for k, v in loaded.items()}
                trace.append(f"session reused from {session_file.name} "
                             "(warmup skipped)")
                return jar, trace
        except (ValueError, OSError):
            trace.append("saved session unreadable; minting a fresh visitor")

    common = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    # HEAD is important: Douyin hands out ttwid / UIFID_TEMP in HEAD response
    # headers, while GET falls through to the JS challenge with only __ac_nonce.
    for label, url, method in (
        ("home", "https://www.douyin.com/", "HEAD"),
        ("video", f"https://www.douyin.com/video/{video_id}/", "HEAD"),
        ("video-get", f"https://www.douyin.com/video/{video_id}/", "GET"),
    ):
        try:
            status, hdrs, body = http_request(url, common, method=method)
        except Exception as exc:  # noqa: BLE001
            trace.append(f"{label}: request failed: {exc}")
            continue
        trace.append(f"{label}: HTTP {status}, {len(body)} bytes")
        for name, value in hdrs:
            if name.lower() != "set-cookie":
                continue
            pair = value.split(";", 1)[0]
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            jar[k.strip()] = v.strip()
    if "s_v_web_id" not in jar:
        jar["s_v_web_id"] = create_s_v_web_id()
        trace.append("s_v_web_id: generated locally (TikTokDownload algorithm)")
    else:
        trace.append("s_v_web_id: supplied by server")
    return jar, trace


def split_pair(part: str) -> tuple[str, str]:
    name, _, value = part.partition("=")
    return unquote(name), unquote(value)


def signed_detail_url(aweme_id: str, jar: dict[str, str]) -> tuple[str, dict[str, str], dict]:
    params = base_params(aweme_id)
    query = urlencode(params)
    bogus = ABogus(UA, browser_info=DEFAULT_BROWSER_INFO).get_value(query)
    query_a = f"{query}&a_bogus={quote(bogus, safe='')}"

    uifid = websign.pick_uifid(jar)
    if not uifid:
        raise RuntimeError("missing UIFID_TEMP / uifid cookie - cannot web-sign")

    pairs = [split_pair(part) for part in query_a.split("&") if part]
    verify_fp = jar.get(websign.VERIFY_FP_COOKIE, "")
    added: dict[str, str] = {}
    if verify_fp:
        for name in websign.VERIFY_FP_PARAMS:
            pairs.append((name, verify_fp))
            added[name] = verify_fp
    signed_query, signature, headers = websign.sign(pairs, uifid)
    added.update({"uifid_prefix": uifid[:12] + "…",
                  "x-secsdk-web-signature": signature,
                  "a_bogus_len": str(len(bogus))})
    return f"https://www.douyin.com{DETAIL_PATH}?{signed_query}", headers, added


def cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def pick_play_urls(detail: dict) -> list[str]:
    out: list[str] = []
    video = detail.get("video") or {}
    for key in ("play_addr", "play_addr_h264", "download_addr", "bit_rate"):
        node = video.get(key)
        if isinstance(node, dict):
            for u in node.get("url_list") or []:
                if u and u not in out:
                    out.append(u)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict):
                    addr = item.get("play_addr") or {}
                    for u in addr.get("url_list") or []:
                        if u and u not in out:
                            out.append(u)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--out", default=str(Path.cwd() / "api"))
    ap.add_argument("--workdir", default="",
                    help="pipeline root; status.json here is the single source of truth")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if a valid mp4 already exists")
    ap.add_argument("--max-urls", type=int, default=6,
                    help="how many candidate play URLs to try (default 6)")
    ap.add_argument("--save-session", action="store_true",
                    help="opt-in: persist visitor cookies to <workdir>/session.json (0600)")
    args = ap.parse_args()

    match = VIDEO_ID_RE.search(args.video_id)
    if not match:
        print(json.dumps({"ok": False, "error": "bad video id"}, ensure_ascii=False))
        return 2
    video_id = match.group(0)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir) if args.workdir else out_dir.parent
    status_file = workdir / "status.json"
    session_file = workdir / "session.json"

    # No plaintext cookie dump by default. Values never enter status.json.
    jar, trace = collect_cookies(video_id, session_file if args.save_session else None)
    result: dict = {"video_id": video_id, "cookie_names": sorted(jar),
                    "warmup": trace, "signing_constants": SIGNING_CONSTANTS}
    if args.save_session:
        session_report = save_secret(session_file, jar)
        result["session_saved"] = session_report
        if not session_report["hardened"]:
            result["session_warning"] = (
                "Visitor cookies were written but ACL restriction failed; "
                "do not sync or share this directory, and delete session.json "
                "when the run is finished."
            )

    def finish(state: str, next_step: str, code: int) -> int:
        result["pipeline_status"] = state
        merge_status(status_file, status=state, next_step=next_step, download=result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code

    try:
        url, sign_headers, added = signed_detail_url(video_id, jar)
    except Exception as exc:  # noqa: BLE001
        result.update({"ok": False, "stage": "signing", "error": str(exc)})
        return finish("signing_failed",
                      "Check the visitor jar (UIFID_TEMP / s_v_web_id) and rerun.", 3)

    result["signed_params"] = added
    result["url_len"] = len(url)

    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"https://www.douyin.com/video/{video_id}/",
        "Cookie": cookie_header(jar),
        "Connection": "close",
        **sign_headers,
    }
    try:
        resp = dy_request(url, headers, timeout=25, retries=2)
    except Exception as exc:  # noqa: BLE001
        result.update({"ok": False, "stage": "request", "error": str(exc),
                       "failure_class": "network_error"})
        return finish("download_failed", "Network error; retry later with backoff.", 4)

    result["http_status"] = resp.status
    result["response_bytes"] = len(resp.body)
    result["attempts"] = resp.attempts
    (out_dir / "detail_raw.txt").write_bytes(resp.body)

    failure_class, hint = classify(resp.status, resp.headers, resp.body)
    result["failure_class"] = failure_class
    if hint:
        result["hint"] = hint
    if failure_class != "ok":
        result["ok"] = False
        result["response_preview"] = resp.body[:300].decode("utf-8", "replace")
        return finish("download_failed", hint or "See failure_class.", 5)

    payload = json.loads(resp.body.decode("utf-8", "replace"))
    (out_dir / "detail.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    detail = payload.get("aweme_detail") or {}
    result.update({"status_code": payload.get("status_code"),
                   "has_aweme_detail": bool(detail),
                   "desc": detail.get("desc"),
                   "author": (detail.get("author") or {}).get("nickname"),
                   "duration_ms": (detail.get("video") or {}).get("duration")})
    urls = pick_play_urls(detail)
    result["play_url_count"] = len(urls)
    (out_dir / "play_urls.json").write_text(
        json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8")

    target = out_dir / f"{video_id}.mp4"
    if args.download:
        if target.is_file() and not args.force:
            existing = validate_media(target.read_bytes())
            if existing["ok"]:
                result.update({"video_file": str(target),
                               "media_validation": existing,
                               "skipped_existing": True})
                result["ok"] = True
                return finish("download_ok",
                              "Valid mp4 already present; transcribe it (1B).", 0)

        attempts_log: list[dict] = []
        for index, media_url in enumerate(urls[:max(1, args.max_urls)]):
            entry: dict = {"index": index}
            try:
                media = dy_request(media_url, {
                    "User-Agent": UA,
                    "Referer": "https://www.douyin.com/",
                    "Accept": "*/*",
                    "Connection": "close",
                }, timeout=60, retries=2)
                entry.update({"status": media.status,
                              "redirects": media.redirects,
                              "attempts": media.attempts,
                              "bytes": len(media.body)})
                check = validate_media(
                    media.body,
                    content_type=media.header("Content-Type"),
                    declared_length=media.header("Content-Length"),
                )
                entry["validation"] = {k: check[k] for k in ("ok", "format", "reason")}
                if check["ok"]:
                    target.write_bytes(media.body)
                    result.update({"video_file": str(target),
                                   "media_validation": check,
                                   "video_sha256": check["sha256"]})
                    attempts_log.append(entry)
                    result["download_attempts"] = attempts_log
                    result["ok"] = True
                    return finish("download_ok",
                                  "Transcribe the mp4 (stage 1B), then distill.", 0)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)[:200]
            attempts_log.append(entry)
        result["download_attempts"] = attempts_log
        result["ok"] = False
        result["failure_class"] = "media_unavailable"
        return finish("download_failed",
                      "No candidate play URL produced a valid mp4; see download_attempts.", 6)

    result["ok"] = True
    return finish("detail_ok", "Re-run with --download, then transcribe.", 0)


if __name__ == "__main__":
    raise SystemExit(main())
