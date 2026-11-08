#!/usr/bin/env python3
r"""Stage 0: turn raw Douyin share text into a verified video_id.

Problem this fixes
------------------
The old stage 0 only ran ``re.search(r"\d{15,25}", input)``.  A real Douyin
share paste contains NO numeric video id at all - only a short code:

    9.74 复制打开抖音，看看【知雪的作品】... https://v.douyin.com/JeNBuAWxQn0/
    06/05 Jvf:/ B@t.eO :1pm

Feeding that to douyin_fetch.py --normalize-only produced ``invalid_input``
and told the user to "provide a 15-25 digit id", even though the link is
perfectly valid.  Reproduced 2026-11-08.

Design borrowed from GitHub (2026-11-08)
----------------------------------------
Evil0ctal/Douyin_TikTok_Download_API:
  src/dtk/urls/expand.py   - first_url(text), per-hop host re-validation,
                             MAX_REDIRECTS=5, loop detection by canonical form
  src/dtk/urls/patterns.py - SHORT_LINK_HOSTS / BARE_URL_HOSTS allowlists
  src/dtk/urls/parse.py    - resolve() as the single entry point

The upstream comment we specifically needed: Douyin's chain is
    v.douyin.com/X -> iesdouyin.com/share/video/<id>?... -> douyin.com/video/<id>
and the last two hops normalize to the SAME resource.  Treating that repeat as
a redirect loop breaks the most common share link there is.  Here, as soon as
a hop yields a video id from a video path, we settle instead of looping.

Security: every hop is re-checked against the host allowlist.  A short link
that redirects to an internal address or another scheme fails closed.

Transport uses http.client directly: this machine's registry proxy breaks
urllib TLS (SSL: UNEXPECTED_EOF_WHILE_READING).

Usage:
    python resolve_input.py "<raw share text or URL>" [--json] [--offline]
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 5
TIMEOUT = 20

ALLOWED_HOSTS = frozenset({
    "v.douyin.com",
    "www.iesdouyin.com", "iesdouyin.com",
    "www.douyin.com", "douyin.com",
})
SHORT_HOSTS = frozenset({"v.douyin.com", "www.iesdouyin.com", "iesdouyin.com"})

URL_RE = re.compile(r"https?://[^\s<>\"'）】\]]+", re.I)
BARE_URL_RE = re.compile(
    r"(?<![\w/])(?:v\.douyin\.com|www\.iesdouyin\.com|iesdouyin\.com|"
    r"www\.douyin\.com|douyin\.com)/[^\s<>\"'）】\]]+", re.I)
# A bare-host reconstruction must not resurrect a non-http pseudo-URL such as
# javascript:alert(1)//v.douyin.com/x into https://v.douyin.com/x.  Caught by
# the 2026-11-08 regression suite.
DANGEROUS_SCHEME_RE = re.compile(r"(?:javascript|data|file|vbscript)\s*:", re.I)
VIDEO_PATH_RE = re.compile(r"/(?:share/)?(?:video|note|slides)/(\d{15,25})", re.I)
PARAM_ID_RE = re.compile(r"(?:modal_id|aweme_id|item_ids|video_id)=(\d{15,25})", re.I)
BARE_ID_RE = re.compile(r"(?<!\d)\d{15,25}(?!\d)")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


class ResolveError(Exception):
    def __init__(self, failure_class: str, message: str, **details):
        super().__init__(message)
        self.failure_class = failure_class
        self.details = details


def first_url(text: str) -> str:
    """Extract the first URL from raw share text, with or without a scheme."""
    m = URL_RE.search(text or "")
    if m:
        return m.group(0).rstrip(".,;:!?)】")
    if DANGEROUS_SCHEME_RE.search(text or ""):
        raise ResolveError("no_url", "non-http pseudo-scheme in input; refusing "
                                     "to reconstruct a bare host into a URL")
    m = BARE_URL_RE.search(text or "")
    if m:
        return "https://" + m.group(0).rstrip(".,;:!?)】")
    raise ResolveError("no_url", "no URL found in the input")


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def assert_allowed(url: str, *, stage: str, hop: int = 0) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ResolveError("scheme_not_allowed", "unsupported URL scheme",
                           stage=stage, hop=hop, url=url)
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ResolveError("redirect_not_allowed" if stage == "hop" else "host_not_allowed",
                           "host is not on the Douyin allowlist",
                           stage=stage, hop=hop, host=host)


def id_from(url: str, body: str = "") -> str:
    """Prefer a video PATH id; share links carry longer author/mid ids too."""
    m = VIDEO_PATH_RE.search(url)
    if m:
        return m.group(1)
    m = PARAM_ID_RE.search(url)
    if m:
        return m.group(1)
    # Only trust a bare numeric id when it is the whole path segment or the
    # entire input looks like an id.  Never "longest number wins": the share
    # chain contains a 21-digit social_author_id that is NOT the video id.
    path = urlsplit(url).path.strip("/")
    if BARE_ID_RE.fullmatch(path):
        return path
    if body and BARE_ID_RE.fullmatch(body.strip()):
        return body.strip()
    return ""


def one_hop(url: str) -> tuple[int, str, str]:
    """Issue exactly ONE request, do not follow redirects. Return (status, Location, body)."""
    parts = urlsplit(url)
    conn = (http.client.HTTPSConnection(parts.netloc, timeout=TIMEOUT)
            if parts.scheme == "https"
            else http.client.HTTPConnection(parts.netloc, timeout=TIMEOUT))
    try:
        path = parts.path + (("?" + parts.query) if parts.query else "")
        conn.request("GET", path, headers={"User-Agent": UA, "Accept": "*/*"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        location = ""
        for key, value in resp.getheaders():
            if key.lower() == "location":
                location = value
                break
        return resp.status, location, body
    finally:
        conn.close()


def resolve(text: str, *, offline: bool = False,
            fetcher=None) -> dict:
    """Return a structured resolution report. Raises ResolveError on failure."""
    raw = (text or "").strip()
    if not raw:
        raise ResolveError("empty_input", "input is empty")

    # Find a URL FIRST and validate its host BEFORE trusting any id inside it.
    # An earlier fast path ran id_from(raw) before assert_allowed(), which let
    # https://evil.example.com/video/<19digits> through as a valid Douyin id.
    # Caught by the 2026-11-08 regression suite; upstream expand.py applies
    # the allowlist at the start AND at every hop for exactly this reason.
    try:
        url = first_url(raw)
    except ResolveError:
        if BARE_ID_RE.fullmatch(raw):
            return {
                "ok": True, "video_id": raw,
                "canonical_url": f"https://www.douyin.com/video/{raw}",
                "input_type": "explicit_id", "hops": 0, "chain": [],
                "expanded": False, "failure_class": "ok",
            }
        raise
    assert_allowed(url, stage="start")

    # A direct /video/<id> or modal_id URL needs no network.
    direct = id_from(url)
    if direct:
        return {
            "ok": True, "video_id": direct,
            "canonical_url": f"https://www.douyin.com/video/{direct}",
            "input_type": "direct_url", "hops": 0, "chain": [url],
            "expanded": False, "failure_class": "ok",
        }

    if offline:
        raise ResolveError("offline_short_link",
                           "short link needs one network hop; rerun without --offline")

    fetch = fetcher or one_hop
    current = url
    canonical_seen: set[str] = set()
    chain: list[dict] = []

    for hop in range(MAX_REDIRECTS):
        status, location, body = fetch(current)
        chain.append({"hop": hop, "url": current, "status": status,
                      "location": location[:200]})

        # A video id can appear in the current URL or in the Location header.
        found = id_from(current) or id_from(location)
        if found:
            return {
                "ok": True, "video_id": found,
                "canonical_url": f"https://www.douyin.com/video/{found}",
                "input_type": "short_link", "hops": hop + 1, "chain": chain,
                "expanded": True, "failure_class": "ok",
            }

        if not location.strip():
            raise ResolveError("unresolved_short_link",
                               "chain ended without exposing a video id",
                               hops=hop + 1, chain=chain)

        candidate = urljoin(current, location.strip())
        assert_allowed(candidate, stage="hop", hop=hop)

        # Canonical repeat = arrived, NOT a loop.  Two URLs resolving to the
        # same video resource are the same destination (upstream expand.py's
        # hard-won lesson).  Here we key on the video id when available, else
        # on scheme-less host+path with share noise stripped.
        canonical = canonical_key(candidate)
        if canonical in canonical_seen:
            raise ResolveError("unresolved_short_link",
                               "redirect chain settled on the same resource",
                               hops=hop + 1, chain=chain, canonical=canonical)
        canonical_seen.add(canonical)
        current = candidate

    raise ResolveError("too_many_redirects", "exceeded MAX_REDIRECTS",
                       max_hops=MAX_REDIRECTS, chain=chain)


def canonical_key(url: str) -> str:
    parts = urlsplit(url)
    vid = VIDEO_PATH_RE.search(parts.path)
    if vid:
        return f"douyin:video:{vid.group(1)}"
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--workdir", default="")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        result = resolve(args.input, offline=args.offline)
    except ResolveError as exc:
        result = {"ok": False, "failure_class": exc.failure_class,
                  "error": str(exc), "details": exc.details}
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        result = {"ok": False, "failure_class": "network_error", "error": str(exc)}

    if args.workdir:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dy_http import merge_status
        workdir = Path(args.workdir)
        merge_status(workdir / "status.json",
                     input_resolution=result,
                     status=("ready_for_signed_download" if result["ok"]
                             else "input_resolution_failed"),
                     next_step=("Run stage 1C douyin_detail.py" if result["ok"]
                                else f"Fix input: {result.get('failure_class')}"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
