#!/usr/bin/env python3
"""Watch ledger: monitored accounts, dedupe, and the采集 queue.

Why this exists
---------------
The single-link workflow starts when a human pastes a URL.  The thing the
video actually builds is different: it *discovers* new work while nobody is
looking.  This script is the bookkeeping half of that loop — who to watch, what
has already been seen, and what is waiting to be transcribed.

Division of labour (learned the hard way, see references/watch-feeds.md)
-----------------------------------------------------------------------
Discovery needs a logged-in session.  Every guest HTTP route to an account's
video list is closed on this machine:

    /aweme/v1/web/aweme/post/    HTTP 200, 0 bytes       (silent risk block)
    iesdouyin /web/api/v2/...    HTTP 200, status 11110  (login required)
    /user/<sec_uid> HTML         6.4 KB captcha page, no SSR data

So the browser does the looking and this script does the remembering:

    agent: open the creator page in Edge -> read the visible DOM -> aweme_ids
    script: `scan-result`  dedupes against seen.json, queues only the new ones

Metadata, cover images, chapters and download all keep using
``douyin_detail.py``, whose ``/aweme/detail/`` endpoint is still open to guests.

Data (all on D:, nothing written to C:)
    <DOUYIN_WATCH_ROOT>/accounts.json   who to watch
    <DOUYIN_WATCH_ROOT>/seen.json       every aweme_id ever seen
    <DOUYIN_WATCH_ROOT>/queue.json      what is waiting / done
    <DOUYIN_WATCH_ROOT>/inbox.txt       manual drop box (one link per line)

Commands
--------
    python watch.py add "<主页链接或 sec_uid>" [--nickname 这很容易]
    python watch.py list
    python watch.py scan-result --account <sec_uid> --ids "id1,id2,id3"
    python watch.py pending [--limit 5]
    python watch.py mark <aweme_id> --status done --run-dir "<R>" [--note-file f]
    python watch.py inbox            # 把 inbox.txt 里的链接/口令收进队列
    python watch.py status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from dy_config import WATCH_ROOT

# WATCH_ROOT comes from dy_config (DOUYIN_WATCH_ROOT).
ACCOUNTS = WATCH_ROOT / "accounts.json"
SEEN = WATCH_ROOT / "seen.json"
QUEUE = WATCH_ROOT / "queue.json"
INBOX = WATCH_ROOT / "inbox.txt"

SEC_UID_RE = re.compile(r"(MS4wLjABAAAA[\w-]{20,})")
VIDEO_ID_RE = re.compile(r"\b(\d{19})\b")
VIDEO_PATH_RE = re.compile(r"/video/(\d{15,25})")

# Six states, not four.  v1.9 shipped with a single `done` covering both "the
# transcript is ready" and "the note is in the knowledge base", and after one
# real batch run 3 of 4 `done` items had no note at all — the ledger claimed
# work that had never happened.  Splitting the terminal state is the fix:
#
#   queued ──► processing ──► collected ──► distilled ──► published
#                 │              (逐字稿就绪)   (笔记通过 lint)  (入库并验收)
#                 └──► failed (retryable / permanent)
#
# `collected` is the state the overnight batch is allowed to leave behind; only
# the morning run may advance an item past it.
STATUSES = ("queued", "processing", "collected", "distilled", "published",
            "skipped", "failed")

# Anything at or beyond `collected` counts as "already fetched" for dedupe.
FETCHED = ("collected", "distilled", "published")

# Celery's visibility_timeout, scaled down: a `processing` item older than this
# is assumed orphaned (laptop slept, process killed, budget abort) and is put
# back in the queue.  Must stay above the slowest real item — a 16-minute video
# took 95 s end to end, so 30 minutes is generous.
STALE_MINUTES = 30
MAX_ATTEMPTS = 3

# Celery's `autoretry_for` lists exactly the transient failures and nothing
# else.  Retrying a deleted video three times just produces a slower failure.
RETRYABLE_HINTS = (
    "timeout", "timed out", "connection", "reset", "temporarily",
    "429", "500", "502", "503", "504", "eof", "ssl", "network", "unreachable",
)
PERMANENT_HINTS = (
    "not found", "不存在", "已删除", "deleted", "404", "403", "private",
    "invalid", "no video_id", "unsupported",
)



# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load(path: Path, key: str) -> dict:
    if not path.is_file():
        return {"version": 1, key: {} if key in ("ids",) else []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"version": 1, key: {} if key in ("ids",) else []}
    data.setdefault(key, {} if key in ("ids",) else [])
    return data


def save(path: Path, data: dict) -> None:
    WATCH_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def norm_sec_uid(raw: str) -> str:
    raw = raw.strip()
    match = SEC_UID_RE.search(raw)
    if match:
        return match.group(1)
    if raw.startswith("MS4w"):
        return raw.split("?")[0].rstrip("/")
    raise SystemExit(f"could not find a sec_uid in: {raw[:80]}")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_add(args) -> int:
    accounts = load(ACCOUNTS, "accounts")
    sec = norm_sec_uid(args.target)
    for row in accounts["accounts"]:
        if row["sec_uid"] == sec:
            if args.nickname:
                row["nickname"] = args.nickname
            save(ACCOUNTS, accounts)
            print(json.dumps({"ok": True, "action": "updated", **row},
                             ensure_ascii=False, indent=2))
            return 0
    row = {
        "sec_uid": sec,
        "nickname": args.nickname or "",
        "url": f"https://www.douyin.com/user/{sec}",
        "added_at": now(),
        "last_scan_at": "",
        "last_seen_count": 0,
        "last_new_count": 0,
    }
    accounts["accounts"].append(row)
    save(ACCOUNTS, accounts)
    print(json.dumps({"ok": True, "action": "added", **row}, ensure_ascii=False, indent=2))
    return 0


def cmd_list(args) -> int:
    accounts = load(ACCOUNTS, "accounts")
    queue = load(QUEUE, "items")
    by_status: dict[str, int] = {}
    for item in queue["items"]:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    print(json.dumps({
        "ok": True,
        "watch_root": str(WATCH_ROOT),
        "account_count": len(accounts["accounts"]),
        "accounts": accounts["accounts"],
        "queue_counts": by_status,
        "queue_total": len(queue["items"]),
    }, ensure_ascii=False, indent=2))
    return 0


def classify_error(text: str) -> str:
    """retryable | permanent | unknown — the Celery autoretry distinction."""
    low = (text or "").lower()
    if any(h in low for h in PERMANENT_HINTS):
        return "permanent"
    if any(h in low for h in RETRYABLE_HINTS):
        return "retryable"
    return "unknown"


def new_item(vid: str, account: str, nickname: str, title: str = "",
             views: str = "") -> dict:
    return {
        "aweme_id": vid, "account": account, "nickname": nickname,
        "title_hint": title, "views_hint": views,
        "first_seen": now(), "status": "queued",
        "attempt": 0, "started_at": "", "last_error": "", "error_class": "",
        "run_dir": "", "note_file": "",
    }


VIEW_HINT_RE = re.compile(r"^(?:共创|置顶|独家)?\d+(?:\.\d+)?[万亿]?$")


def parse_scan_items(raw: str) -> list[tuple[str, str, str]]:
    """Parse ``id~标题~播放量|id~标题~播放量`` into (id, title, views).

    The browser agent types this by hand from the DOM, so the format has to
    survive the two characters that actually show up in douyin titles.  Both of
    them used to lose videos silently:

    * a ``|`` inside a title split one video into two chunks and the id-less
      half was dropped - no warning, the video simply never entered the queue;
    * an extra ``~`` pushed the view count out of position, so a video kept a
      truncated title and no view hint.

    So: a chunk with no id is treated as the *continuation* of the previous
    title (its trailing view hint, if any, moves back onto that item), and the
    view hint is taken positionally from the tail instead of trusting index 1.
    """
    items: list[tuple[str, str, str]] = []
    for chunk in (raw or "").split("|"):
        parts = [p.strip() for p in chunk.split("~")]
        vid = next((p for p in parts if p.isdigit() and len(p) >= 15), "")

        if vid:
            rest = [p for p in parts if p != vid and p]
            views = ""
            if len(rest) >= 2 and VIEW_HINT_RE.match(rest[-1]):
                views = rest.pop()
            items.append((vid, "~".join(rest).strip(), views))
            continue

        tail = [p for p in parts if p]
        if not items or not tail:
            continue                    # trailing separators / empty chunk

        prev_id, prev_title, prev_views = items[-1]
        views = prev_views
        if not views and len(tail) >= 2 and VIEW_HINT_RE.match(tail[-1]):
            views = tail.pop()
        piece = "~".join(tail)
        title = f"{prev_title}|{piece}".strip("|")
        items[-1] = (prev_id, title, views)
    return items


def extract_works_from_dom(text: str) -> list[tuple[str, str, str]]:
    """Parse a douyin user-page DOM snapshot into (id, title, views).

    The snapshot (``dom_cua_get_visible_dom`` on a *logged-in* Edge tab) renders
    one ``<a>`` per work card, e.g.::

        <a node_id=34 href="/video/7682633651724569891">置顶42.0万抄底拼多多…#这很容易…</a>

    A guest tab only yields the sign-in modal and ~1 card (the modal occludes
    the rest), so callers MUST use the logged-in Edge.  Snapshot quirks handled
    here, all observed on 2026-11-08:

    * the href is a *relative* path (``/video/…``), not ``https://…`` — we keep
      only the 19-digit id;
    * the card text repeats the whole title+hashtags *twice* — we take up to the
      first ``#``;
    * the view hint has several shapes — ``置顶42.0万`` / ``共创1.8万`` / ``1937``
      / ``1.6万`` — captured whole (prefix kept so callers can skip 置顶/共创
      pins, which are not "posted today").
    """
    out: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()
    card_re = re.compile(r'/video/(\d{15,19})"[^>]*>([^<]*)</a>')
    for m in card_re.finditer(text or ""):
        vid = m.group(1)
        if vid in seen_ids:
            continue
        seen_ids.add(vid)
        raw = m.group(2).strip()
        vm = re.match(r'^(置顶|共创|独家)?\s*(\d+(?:\.\d+)?(?:万|亿)?)\s*', raw)
        views = vm.group(0).strip() if vm else ""
        body = raw[vm.end():] if vm else raw
        if "#" in body:
            title = body.split("#", 1)[0].strip()
        else:
            half = len(body) // 2
            title = body[:half].strip() if half and body[:half] == body[half:] else body.strip()
        out.append((vid, title, views))
    return out


def _record_scan(sec: str, parsed: list[tuple[str, str, str]], nickname: str) -> dict:
    """Dedupe + enqueue + report. Shared by ``scan-result`` and ``scan-dom``."""
    # One snapshot can list the same video twice: a pinned item is rendered in
    # two groups, or the agent reads a card twice.  ``seen`` only guards against
    # *earlier* scans, so a duplicate inside this batch used to pass straight
    # through - the same video was downloaded (100 MB, ~90 s) and distilled
    # twice.  Collapse here, keeping the richer of the two records.
    scanned_total = len(parsed)
    unique: dict[str, tuple[str, str, str]] = {}
    for vid, title, views in parsed:
        prev = unique.get(vid)
        unique[vid] = (vid, prev[1] or title, prev[2] or views) if prev else (vid, title, views)
    parsed = list(unique.values())
    duplicates = scanned_total - len(parsed)

    accounts = load(ACCOUNTS, "accounts")
    seen = load(SEEN, "ids")
    queue = load(QUEUE, "items")

    row = next((a for a in accounts["accounts"] if a["sec_uid"] == sec), None)
    if row is None:
        row = {"sec_uid": sec, "nickname": nickname or "",
               "url": f"https://www.douyin.com/user/{sec}", "added_at": now()}
        accounts["accounts"].append(row)

    fresh, known = [], []
    for vid, title, views in parsed:
        # A previously skipped item that shows up again is still "seen"; only a
        # never-seen id is new.  Failed items are handled by reconcile, not here.
        if seen["ids"].get(vid):
            known.append(vid)
        else:
            fresh.append(vid)

    for vid, title, views in parsed:
        if vid not in fresh:
            continue
        seen["ids"][vid] = {"account": sec, "first_seen": now(), "status": "queued"}
        queue["items"].append(new_item(vid, sec, row.get("nickname", ""), title, views))

    row.update({"last_scan_at": now(), "last_seen_count": len(parsed),
                "last_new_count": len(fresh),
                "nickname": nickname or row.get("nickname", "")})
    save(ACCOUNTS, accounts)
    save(SEEN, seen)
    save(QUEUE, queue)

    return {
        "ok": True, "account": sec, "nickname": row.get("nickname", ""),
        "scanned": scanned_total, "duplicates_in_scan": duplicates,
        "new": len(fresh), "already_seen": len(known),
        "new_ids": fresh,
        "queue_pending": sum(1 for i in queue["items"] if i["status"] == "queued"),
    }


def enqueue_search_videos(videos: list[dict]) -> dict:
    """Enqueue whole-site search results, deduped against seen.

    Each video dict: ``video_id, title, views, author_sec_uid,
    author_nickname, web_url, create_time, likes, comments``.  The author's
    ``sec_uid`` becomes the queue's ``account`` dimension (so it shows up in
    ``status``/``list``), tagged ``discovered_via=search``.  Videos with no
    valid author sec_uid are skipped — a search-only item has no account to tie
    dedupe/notes to, and fabricating one would corrupt the ledger.
    """
    if not videos:
        return {"ok": True, "new": 0, "already_seen": 0, "skipped": 0, "new_ids": []}

    accounts = load(ACCOUNTS, "accounts")
    seen = load(SEEN, "ids")
    queue = load(QUEUE, "items")

    acc_index = {a["sec_uid"]: a for a in accounts["accounts"]}
    fresh, known, skipped = [], [], []

    for v in videos:
        vid = str(v.get("video_id") or "").strip()
        if not vid:
            continue
        sec = str(v.get("author_sec_uid") or "").strip()
        match = SEC_UID_RE.search(sec)
        if not match:
            skipped.append(vid)
            continue
        sec = match.group(1)
        nick = str(v.get("author_nickname") or "").strip()
        title = str(v.get("title") or "").strip()
        views = str(v.get("views") or "").strip()

        if vid in seen["ids"]:
            known.append(vid)
        else:
            fresh.append(vid)
            seen["ids"][vid] = {"account": sec, "first_seen": now(), "status": "queued"}
            item = new_item(vid, sec, nick, title, views)
            if v.get("create_time"):
                item["create_time"] = v["create_time"]
            if v.get("likes"):
                item["likes"] = v["likes"]
            if v.get("comments"):
                item["comments"] = v["comments"]
            if v.get("web_url"):
                item["web_url"] = v["web_url"]
            item["discovered_via"] = "search"
            queue["items"].append(item)

        # make sure the author account exists for status/list roll-ups
        row = acc_index.get(sec)
        if row is None:
            row = {"sec_uid": sec, "nickname": nick,
                   "url": f"https://www.douyin.com/user/{sec}",
                   "added_at": now(), "last_scan_at": "", "last_seen_count": 0,
                   "last_new_count": 0, "discovered_via": "search"}
            accounts["accounts"].append(row)
            acc_index[sec] = row

    save(ACCOUNTS, accounts)
    save(SEEN, seen)
    save(QUEUE, queue)
    return {
        "ok": True, "source": "search", "new": len(fresh),
        "already_seen": len(known), "skipped": len(skipped),
        "new_ids": fresh,
        "queue_pending": sum(1 for i in queue["items"] if i["status"] == "queued"),
    }


def cmd_scan_search(args) -> int:
    """Record whole-site search results from a JSON file produced by
    ``search_discover.py`` (which drives the vendored zhubaohe123/Douyin-api
    search engine)."""
    p = Path(args.json)
    if not p.is_file():
        raise SystemExit(f"JSON 文件不存在: {args.json}")
    try:
        videos = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise SystemExit(f"JSON 解析失败: {e}")
    if not isinstance(videos, list):
        raise SystemExit("JSON 顶层必须是数组（搜索结果列表）")
    print(json.dumps(enqueue_search_videos(videos), ensure_ascii=False, indent=2))
    return 0


def cmd_scan(args) -> int:
    """Record one page scan. Only unseen ids enter the queue.

    ``--items`` carries what the browser already saw (title, view count) so the
    queue is filterable before anything is downloaded.  v1.9 accepted bare ids
    and left ``title_hint`` empty on all 13 items — the only way to learn what a
    video was about was to spend 100 MB and 90 s on it.
    """
    sec = norm_sec_uid(args.account)
    parsed: list[tuple[str, str, str]] = []
    if args.items:
        parsed = parse_scan_items(args.items)
    else:
        parsed = [(i, "", "") for i in re.split(r"[,\s]+", args.ids or "")
                  if i.strip().isdigit() and len(i.strip()) >= 15]
    if not parsed:
        raise SystemExit("no valid aweme_id in --items / --ids")
    print(json.dumps(_record_scan(sec, parsed, args.nickname), ensure_ascii=False, indent=2))
    return 0


def cmd_scan_dom(args) -> int:
    """Record a scan from a DOM snapshot file (logged-in Edge tab).

    Usage: open the user page in the *logged-in* Edge, run
    ``dom_cua_get_visible_dom``, save the returned text to a file, then::

        watch.py scan-dom --account <sec_uid> --dom-file <file>

    This avoids hand-typing ``--items`` and silently losing titles that contain
    ``|`` (the chunk separator).  A guest tab only sees ~1 card behind the
    sign-in modal, so the file must come from a logged-in session.
    """
    sec = norm_sec_uid(args.account)
    p = Path(args.dom_file)
    if not p.is_file():
        raise SystemExit(f"DOM 文件不存在: {args.dom_file}")
    parsed = extract_works_from_dom(p.read_text(encoding="utf-8"))
    if not parsed:
        raise SystemExit("DOM 里没解析到 /video/ 链接（确认是登录态的 Edge，且页面已渲染作品卡片）")
    print(json.dumps(_record_scan(sec, parsed, args.nickname), ensure_ascii=False, indent=2))
    return 0


def cmd_inbox(args) -> int:
    """Manual drop box: the no-browser fallback for discovery."""
    path = Path(args.file) if args.file else INBOX
    if not path.is_file():
        print(json.dumps({"ok": True, "added": 0, "reason": f"{path} not found"},
                         ensure_ascii=False, indent=2))
        return 0
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]

    seen = load(SEEN, "ids")
    queue = load(QUEUE, "items")
    added, skipped = [], []
    for line in lines:
        match = VIDEO_PATH_RE.search(line) or VIDEO_ID_RE.search(line)
        if not match:
            skipped.append({"line": line[:60], "reason": "no aweme_id (short link? use run_pipeline directly)"})
            continue
        vid = match.group(1)
        if vid in seen["ids"]:
            skipped.append({"line": line[:60], "reason": "already seen"})
            continue
        seen["ids"][vid] = {"account": "inbox", "first_seen": now(), "status": "queued"}
        queue["items"].append({"aweme_id": vid, "account": "inbox", "nickname": "手工投递",
                               "first_seen": now(), "status": "queued", "run_dir": "",
                               "note_file": "", "title_hint": ""})
        added.append(vid)
    save(SEEN, seen)
    save(QUEUE, queue)
    print(json.dumps({"ok": True, "added": added, "skipped": skipped,
                      "queue_pending": sum(1 for i in queue["items"] if i["status"] == "queued")},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_pending(args) -> int:
    queue = load(QUEUE, "items")
    items = [i for i in queue["items"] if i["status"] == "queued"]
    if args.limit:
        items = items[: args.limit]
    print(json.dumps({"ok": True, "count": len(items), "items": items},
                     ensure_ascii=False, indent=2))
    return 0


def sha256_file(path: Path) -> str:
    """Content digest, streamed so a large note never lands entirely in RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_matches(note_path: Path, recorded: dict) -> tuple[bool, str]:
    """Does the file on disk still hold the bytes that were published?

    Nextflow spells this trade-off out as two cache modes: ``cache 'lenient'``
    hashes size and mtime, and its own docs warn that it *"can miss same-size
    content changes"*; ``cache 'deep'`` hashes content.  v1.10 compared
    ``verified_file_size`` against the current size — it ran lenient — so a note
    rewritten to the same length would still have been called published.

    Hash whenever a hash was recorded; keep size only as a fallback for runs
    that predate it.  DVC's ``dvc status`` names this situation ``modified``,
    which is why a failure still returns an explanation instead of a bare False.
    """
    actual_sha = sha256_file(note_path)
    actual_bytes = note_path.stat().st_size
    want_sha = str(recorded.get("sha256") or recorded.get("verified_note_sha256")
                   or "").strip().lower()
    want_bytes = recorded.get("bytes")
    if want_bytes in (None, ""):
        want_bytes = recorded.get("verified_file_size")

    if want_sha:
        if want_sha == actual_sha:
            return True, f"sha256 {actual_sha[:12]} 与入库版本一致"
        return False, (f"本地笔记已被改写：sha256 {actual_sha[:12]} ≠ 入库版本 "
                       f"{want_sha[:12]}（现 {actual_bytes:,} bytes），需重新 finalize")
    if isinstance(want_bytes, int) and want_bytes:
        if want_bytes == actual_bytes:
            return True, f"字节数 {actual_bytes:,} 一致（旧记录无 sha256，仅按大小比对）"
        return False, (f"本地笔记已变：{actual_bytes:,} bytes ≠ 入库 "
                       f"{want_bytes:,} bytes，需重新 finalize")
    return False, "没有可用的入库指纹"


def derive_state(item: dict) -> tuple[str, str]:
    """Work out the real state from artefacts on disk.

    yt-dlp's archive file and Nextflow's `-resume` share one idea: trust the
    output, not the bookkeeping.  A ledger field can be written by a process
    that then dies; a note file either exists or it does not.  Returns
    (state, evidence).
    """
    run_dir = Path(item.get("run_dir") or "")
    if not item.get("run_dir") or not run_dir.is_dir():
        return item.get("status", "queued"), "no run dir"
    status = {}
    status_file = run_dir / "status.json"
    if status_file.is_file():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            status = {}
    notes = [p for p in run_dir.glob("*.md")
             if p.name != "STATUS.md" and "素材卡" not in p.name]
    lint = {}
    lint_file = run_dir / "lint-note.json"
    if lint_file.is_file():
        try:
            lint = json.loads(lint_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            lint = {}
    ima = status.get("ima") or {}
    # v1.10 kept the upload fingerprint in `note` (written by hand) while the
    # proof lived under `ima` (written by finalize).  Accept either so old runs
    # are still gradeable.
    recorded = dict(status.get("note") or {})
    if ima.get("verified_note_sha256"):
        recorded.setdefault("sha256", ima["verified_note_sha256"])
    if ima.get("verified_file_size"):
        recorded.setdefault("bytes", ima["verified_file_size"])
    has_fingerprint = bool(recorded.get("sha256")) or isinstance(
        recorded.get("bytes"), int)

    note = notes[0] if notes else None
    matched, why = (False, "无笔记可比对")
    if note:
        matched, why = content_matches(note, recorded)
    put_ok = ima.get("put_status") in (200, 201, 204)
    # `verified` was only introduced in v1.8's finalize; older runs recorded the
    # same proof under verified_file_size / verified_knowledge_total_size.
    # Absence of the new key is not absence of evidence.
    strong = bool(ima.get("verified") or (ima.get("receipt") or {}).get("ok"))

    if note and strong:
        if not has_fingerprint:
            return "published", f"{note.name} + ima 入库已验证（该 run 无指纹可比对）"
        if matched:
            return "published", f"{note.name} + ima 入库已验证（{why}）"
        # "Verified" describes the past.  If the bytes changed afterwards, the
        # remote copy is stale and the ledger must stop vouching for it — this
        # is exactly how run6's note lost 226 bytes while staying "verified".
        return "distilled", f"{note.name} + ⚠ {why}"
    if note and put_ok and matched:
        return "published", f"{note.name} + ima PUT {ima.get('put_status')}（{why}）"
    # A recorded fingerprint that no longer matches is drift, and drift outranks
    # "lint passed": the note may be perfect locally while ima holds another
    # version.  Report that instead of a reassuring PASS.
    if note and has_fingerprint and not matched:
        return "distilled", f"{note.name} + ⚠ {why}"
    if note and lint.get("ok"):
        return "distilled", f"{note.name} + lint PASS"
    if note:
        return "distilled", f"{note.name} (lint 未记录)"
    if (run_dir / "source" / "transcript.md").is_file() or (run_dir / "api").is_dir():
        return "collected", "media/transcript present, no note yet"
    return item.get("status", "queued"), "run dir exists but empty"


def cmd_reconcile(args) -> int:
    """Repair the ledger before trusting it.

    Three jobs, all borrowed from Celery/yt-dlp behaviour:

    1. **Reclaim orphans.**  A `processing` item whose `started_at` is older
       than STALE_MINUTES means the runner died (sleep, kill, budget abort).
       Celery calls this the visibility timeout; without it the item is stuck
       forever, which is exactly what v1.9 did.
    2. **Promote from artefacts.**  `collected`/`processing` items are re-graded
       by what is actually on disk, so a killed run that *did* finish is not
       redone.
    3. **Retry the retryable.**  `failed` items under MAX_ATTEMPTS whose error
       class is transient go back to `queued`.
    """
    queue = load(QUEUE, "items")
    seen = load(SEEN, "ids")
    accounts = {a["sec_uid"]: a for a in load(ACCOUNTS, "accounts")["accounts"]}
    changes: list[dict] = []
    # Field-level repairs (title backfill) do not appear in `changes`, so gating
    # the save on that list silently threw them away.  Track dirt separately.
    dirty = False

    def record(item, before, after, why):
        changes.append({"aweme_id": item["aweme_id"], "from": before,
                        "to": after, "why": why})

    for item in queue["items"]:
        before = item["status"]
        item.setdefault("attempt", 0)

        # Migrate v1.9's overloaded `done`: it meant "the batch fetched it",
        # which is now `collected`.  Re-derive from disk right after.
        if before == "done":
            item["status"] = "collected"
            record(item, "done", "collected", "旧状态迁移：done 拆成 collected 起算")
            before = "collected"

        if before == "processing":
            started = item.get("started_at") or ""
            age = None
            if started:
                try:
                    age = (datetime.now()
                           - datetime.fromisoformat(started)).total_seconds() / 60
                except ValueError:
                    age = None
            if age is None or age > STALE_MINUTES:
                item.update({"status": "queued",
                             "last_error": f"回收：processing 超过 "
                                           f"{STALE_MINUTES} 分钟无进展"
                                           + (f"（{age:.0f} 分钟）" if age else ""),
                             "error_class": "stale"})
                record(item, before, "queued", item["last_error"])
                before = "queued"
            else:
                continue

        if before == "failed":
            if (item.get("error_class") in ("retryable", "stale")
                    and item["attempt"] < MAX_ATTEMPTS):
                item["status"] = "queued"
                record(item, before, "queued",
                       f"重试（第 {item['attempt']}/{MAX_ATTEMPTS} 次，"
                       f"{item.get('error_class')}）")
                before = "queued"
            else:
                continue

        # `distilled` is included so a note that was later verified can move up to
        # `published`; without it the promotion was unreachable.  `published` is
        # re-graded too: v1.10 treated it as terminal, which is how a note could
        # be edited after upload and stay "published" forever.
        if before in ("queued", "processing", "collected", "distilled", "published"):
            derived, evidence = derive_state(item)
            if derived != before:
                item["status"] = derived
                dirty = True
                record(item, before, derived, evidence)
            # Keep the drift visible even when the state happens to be right,
            # so the morning brief can report "N 条笔记在入库后被改过".
            if "本地笔记已" in evidence:
                if item.get("note_drift") != evidence:
                    item["note_drift"] = evidence
                    dirty = True
            elif item.pop("note_drift", None) is not None:
                dirty = True

        # Backfill what the browser never told us: once detail.json exists,
        # the queue can show a real title instead of a bare id.
        if not item.get("title_hint") and item.get("run_dir"):
            detail = Path(item["run_dir"]) / "api" / "detail.json"
            if detail.is_file():
                try:
                    payload = json.loads(detail.read_text(encoding="utf-8"))
                    aweme = payload.get("aweme_detail") or payload
                    desc = str(aweme.get("desc") or "").splitlines()
                    if desc and desc[0].strip():
                        item["title_hint"] = desc[0].strip()[:80]
                        dirty = True
                    author = (aweme.get("author") or {}).get("nickname") or ""
                    if author and not item.get("nickname"):
                        item["nickname"] = author
                        dirty = True
                except (ValueError, OSError):
                    pass

        if item["aweme_id"] in seen["ids"]:
            seen["ids"][item["aweme_id"]]["status"] = item["status"]

    # Account roll-up so a silent account (renamed / banned / no output) shows up.
    for sec, acc in accounts.items():
        mine = [i for i in queue["items"] if i.get("account") == sec]
        acc["queue_total"] = len(mine)
        acc["queue_pending"] = sum(1 for i in mine if i["status"] in ("queued", "processing"))
        acc["queue_published"] = sum(1 for i in mine if i["status"] == "published")

    if not args.dry_run and (changes or dirty):
        save(QUEUE, queue)
        save(SEEN, seen)
        save(ACCOUNTS, {"version": 1, "accounts": list(accounts.values())})

    from collections import Counter  # noqa: PLC0415
    print(json.dumps({
        "ok": True, "dry_run": args.dry_run, "stale_minutes": STALE_MINUTES,
        "changes": changes, "change_count": len(changes),
        "by_status": dict(Counter(i["status"] for i in queue["items"])),
        "hint": "加 -f 之外的参数不会写盘" if args.dry_run else "",
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_mark(args) -> int:
    if args.status not in STATUSES:
        raise SystemExit(f"--status must be one of {STATUSES}")
    queue = load(QUEUE, "items")
    seen = load(SEEN, "ids")
    hit = None
    for item in queue["items"]:
        if item["aweme_id"] == args.aweme_id:
            hit = item
            break
    if hit is None:
        hit = new_item(args.aweme_id, "", "", "")
        queue["items"].append(hit)
    before = hit["status"]
    hit["status"] = args.status
    hit["updated_at"] = now()
    hit.setdefault("attempt", 0)
    if args.status == "processing":
        hit["started_at"] = now()
        hit["attempt"] = hit["attempt"] + 1
    if args.run_dir:
        hit["run_dir"] = args.run_dir
    if args.note_file:
        hit["note_file"] = args.note_file
    if args.reason:
        hit["reason"] = args.reason
        if args.status == "failed":
            hit["last_error"] = args.reason
            hit["error_class"] = classify_error(args.reason)
    if args.aweme_id in seen["ids"]:
        seen["ids"][args.aweme_id]["status"] = args.status
    save(QUEUE, queue)
    save(SEEN, seen)
    print(json.dumps({"ok": True, "from": before, "item": hit},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_forget(args) -> int:
    """Drop an id from queue + seen. For bad inbox entries and test data.

    yt-dlp's archive is "append-only and trivially repairable — delete a line to
    force a re-download"; this is the same affordance.  Anything removed from
    `seen` becomes discoverable again, so it is also how you re-queue a video
    you previously skipped.
    """
    queue = load(QUEUE, "items")
    seen = load(SEEN, "ids")
    before = len(queue["items"])
    queue["items"] = [i for i in queue["items"] if i["aweme_id"] != args.aweme_id]
    seen["ids"].pop(args.aweme_id, None)
    removed_queue = before - len(queue["items"])
    save(QUEUE, queue)
    save(SEEN, seen)
    print(json.dumps({"ok": True, "aweme_id": args.aweme_id,
                      "removed_from_queue": removed_queue,
                      "removed_from_seen": True,
                      "queue_total": len(queue["items"])},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    accounts = load(ACCOUNTS, "accounts")
    seen = load(SEEN, "ids")
    queue = load(QUEUE, "items")
    counts: dict[str, int] = {}
    for item in queue["items"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    today = datetime.now().strftime("%Y-%m-%d")
    today_new = [i for i in queue["items"] if i.get("first_seen", "").startswith(today)]
    # The funnel is the point: `collected` is work the batch finished that the
    # morning run still owes a note for.
    funnel = {s: counts.get(s, 0) for s in
              ("queued", "processing", "collected", "distilled", "published",
               "failed", "skipped")}
    print(json.dumps({
        "ok": True, "date": today,
        "accounts": len(accounts["accounts"]),
        "seen_total": len(seen["ids"]),
        "queue_total": len(queue["items"]),
        "by_status": counts,
        "funnel": funnel,
        "needs_distillation": funnel["collected"],
        "today_discovered": len(today_new),
        "today_pending": sum(1 for i in today_new
                             if i["status"] in ("queued", "processing", "collected")),
        "watched": [{"nickname": a.get("nickname", ""), "sec_uid": a["sec_uid"][:24] + "…",
                     "last_scan": a.get("last_scan_at", "never"),
                     "last_new": a.get("last_new_count", 0),
                     "pending": a.get("queue_pending", 0),
                     "published": a.get("queue_published", 0)} for a in accounts["accounts"]],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="抖音监控台账")
    sub = ap.add_subparsers(dest="command", required=True)

    a = sub.add_parser("add", help="加入监控清单")
    a.add_argument("target", help="主页链接或 sec_uid")
    a.add_argument("--nickname", default="")
    a.set_defaults(func=cmd_add)

    lp = sub.add_parser("list", help="清单与队列概览")
    lp.set_defaults(func=cmd_list)

    sc = sub.add_parser("scan-result", help="记录一次浏览器扫描结果")
    sc.add_argument("--account", required=True)
    sc.add_argument("--ids", default="", help="逗号/空格分隔的 aweme_id（无标题时用）")
    sc.add_argument("--items", default="",
                    help="带标题的写法：'id~标题~播放量|id~标题~播放量'")
    sc.add_argument("--nickname", default="")
    sc.set_defaults(func=cmd_scan)

    sd = sub.add_parser("scan-dom", help="从 DOM 快照文件记录扫描（登录态 Edge）")
    sd.add_argument("--account", required=True)
    sd.add_argument("--dom-file", required=True, help="dom_cua_get_visible_dom 输出的文本文件")
    sd.add_argument("--nickname", default="")
    sd.set_defaults(func=cmd_scan_dom)

    sq = sub.add_parser("scan-search", help="从全站搜索结果 JSON 入队（搜索发现通道）")
    sq.add_argument("--json", required=True, help="search_discover.py 产出的搜索结果 JSON 文件")
    sq.set_defaults(func=cmd_scan_search)

    rc = sub.add_parser("reconcile", help="修台账：回收卡死、按产物推状态、重试可重试项")
    rc.add_argument("--dry-run", action="store_true", default=False,
                    help="只报告不写盘")
    rc.set_defaults(func=cmd_reconcile)

    ib = sub.add_parser("inbox", help="收 inbox.txt 里的链接")
    ib.add_argument("--file", default="")
    ib.set_defaults(func=cmd_inbox)

    pd = sub.add_parser("pending", help="待处理队列")
    pd.add_argument("--limit", type=int, default=0)
    pd.set_defaults(func=cmd_pending)

    mk = sub.add_parser("mark", help="标记某条状态")
    mk.add_argument("aweme_id")
    mk.add_argument("--status", required=True)
    mk.add_argument("--run-dir", default="")
    mk.add_argument("--note-file", default="")
    mk.add_argument("--reason", default="")
    mk.set_defaults(func=cmd_mark)

    st = sub.add_parser("status", help="当天汇总")
    st.set_defaults(func=cmd_status)

    fg = sub.add_parser("forget", help="从队列与 seen 中删除一个 id")
    fg.add_argument("aweme_id")
    fg.set_defaults(func=cmd_forget)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
