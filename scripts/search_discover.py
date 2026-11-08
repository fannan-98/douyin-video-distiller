#!/usr/bin/env python3
"""Whole-site search discovery for the distill pipeline.

Why this exists
---------------
`watch.py scan-dom` discovers new videos from a *fixed list of accounts* you
already watch.  But the original ask was "全站最近 30 天新出的赚钱方法" — you
don't know the authors yet, you only know the topic.  That needs search, and
Douyin's search API is closed to guests (see SKILL.md §三·补 "关键词搜索").

The vendored `zhubaohe123/Douyin-api` (MIT, at
`D:/douyin-distill/_vendor/douyin-api`) drives a real Chromium that intercepts
the official search XHR — it only needs a one-time QR login into a *fresh*
profile (never reads your Edge cookie; see SKILL.md red line #15).  This script
is the thin bridge: run the search → normalise → hand the list to
`watch.py scan-search`, which dedupes against seen.json and enqueues.

Time filtering
--------------
Douyin's own `publish_time` only supports 0/1/7/180 days, so there is no
native "30 days".  We pull `publish_time=180` (broad) with `sort_type=2`
(newest first) and then filter locally by `create_time` within `--since-days`
(default 30).  All of that lives in the vendored `douyin_searcher.search()`.

Usage
-----
    # first time only: QR login into the tool's own profile
    cd D:/douyin-distill/_vendor/douyin-api
    ../.venv/Scripts/python.exe login.py

    # discovery (reuses cookies.txt from the login above)
    ../.venv/Scripts/python.exe search_discover.py --keyword "赚钱方法" --since-days 30

    # multi-page sweep, no auto-enqueue (just dump JSON for inspection)
    ../.venv/Scripts/python.exe search_discover.py --keyword "副业" --pages 3 --no-enqueue
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- locate the vendored search engine -------------------------------------
VENDOR_DIR = Path(r"D:/douyin-distill/_vendor/douyin-api")
if not VENDOR_DIR.is_dir():
    raise SystemExit(f"找不到 vendored 搜索引擎目录: {VENDOR_DIR}")
sys.path.insert(0, str(VENDOR_DIR))

# `DouyinSearcher` only needs playwright, which lives in the vendored venv.
from douyin_searcher import DouyinSearcher  # noqa: E402

WATCH_PY = Path(__file__).resolve().parent / "watch.py"
WATCH_ROOT = Path(r"D:/douyin-distill/_watch")


def normalize(video: dict) -> dict:
    """Map one vendored search result into the queue envelope."""
    author = video.get("author") or {}
    return {
        "video_id": str(video.get("video_id") or "").strip(),
        "title": (video.get("title") or "").strip(),
        "views": (video.get("views") or "").strip(),
        "author_sec_uid": (author.get("sec_uid") or "").strip(),
        "author_nickname": (author.get("name") or "").strip(),
        "web_url": (video.get("web_url") or "").strip(),
        "create_time": video.get("create_time") or 0,
        "likes": video.get("likes") or 0,
        "comments": video.get("comments") or 0,
    }


async def run_search(keyword: str, since_days: int, publish_time: int,
                     sort_type: int, pages: int, headless: bool,
                     human_wait: int = 180) -> list[dict]:
    searcher = DouyinSearcher(headless=headless)
    # Keep cookies + intercepted JSON in the vendor dir, consistent with login_auto.py
    searcher.cookie_file = str(VENDOR_DIR / "cookies.txt")
    collected: list[dict] = []
    seen_ids: set[str] = set()
    try:
        for page in range(1, pages + 1):
            result = await searcher.search(
                keyword=keyword, page_num=page,
                publish_time=publish_time, sort_type=sort_type,
                since_days=since_days, human_captcha_wait=human_wait,
            )
            if not result.get("success"):
                code = result.get("error")
                msg = result.get("message", "")
                print(f"[page {page}] 搜索失败: {code} - {msg}", file=sys.stderr)
                if code == "CAPTCHA_REQUIRED":
                    print("   若出现滑块验证：请在弹出的浏览器中手动完成，脚本会等待；"
                          "若超时，可重跑本命令。cookie 失效则重登录：", file=sys.stderr)
                    print("   cd D:/douyin-distill/_vendor/douyin-api "
                          "&& .venv/Scripts/python.exe login_auto.py", file=sys.stderr)
                break
            videos = (result.get("data") or {}).get("videos") or []
            if not videos:
                print(f"[page {page}] 无更多结果，停止翻页。", file=sys.stderr)
                break
            for v in videos:
                vid = str(v.get("video_id") or "")
                if vid in seen_ids:
                    continue
                seen_ids.add(vid)
                collected.append(normalize(v))
            print(f"[page {page}] 拉取 {len(videos)} 条，去重后累计 {len(collected)} 条",
                  file=sys.stderr)
    finally:
        await searcher.stop()
    return collected


def main() -> int:
    ap = argparse.ArgumentParser(description="全站关键词搜索发现（接 watch.py scan-search）")
    ap.add_argument("--keyword", required=True, help="搜索词，如 赚钱方法 / 副业 / 信息差")
    ap.add_argument("--since-days", type=int, default=30, help="本地过滤：仅保留近 N 天发布")
    ap.add_argument("--publish-time", type=int, default=180,
                    help="抖音时间筛选: 0不限/1一天内/7一周内/180半年内（拉宽后再本地精筛）")
    ap.add_argument("--sort-type", type=int, default=2, help="排序: 0综合/1最多点赞/2最新发布")
    ap.add_argument("--pages", type=int, default=1, help="翻页数（每页约 10–20 条）")
    ap.add_argument("--headless", action="store_true", default=False,
                    help="无头模式（需 cookie 有效；否则易触发验证码，建议默认有头）")
    ap.add_argument("--human-wait", type=int, default=180,
                    help="出现滑块验证码时，等待用户手动完成的最长秒数（默认 180）")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径（默认 _watch/search-<词>-<日期>.json）")
    ap.add_argument("--no-enqueue", action="store_true", default=False,
                    help="只导出 JSON，不调用 watch.py 入队")
    args = ap.parse_args()

    # Run from the vendor dir so cookies.txt / raw_intercepted.json land where
    # login.py expects them.
    os.chdir(VENDOR_DIR)

    out_path = args.out or str(
        WATCH_ROOT / f"search-{args.keyword}-{datetime.now():%Y%m%d}.json"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    videos = asyncio.run(run_search(
        keyword=args.keyword, since_days=args.since_days,
        publish_time=args.publish_time, sort_type=args.sort_type,
        pages=args.pages, headless=args.headless,
        human_wait=args.human_wait,
    ))

    Path(out_path).write_text(
        json.dumps(videos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"搜索结果已写出: {out_path}（{len(videos)} 条）", file=sys.stderr)

    if args.no_enqueue:
        print(json.dumps({"ok": True, "wrote": out_path, "count": len(videos),
                          "enqueued": False}, ensure_ascii=False, indent=2))
        return 0

    # Hand off to watch.py scan-search (dedupe + enqueue).
    proc = subprocess.run(
        [sys.executable, str(WATCH_PY), "scan-search", "--json", out_path],
        capture_output=True, text=True,
    )
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
