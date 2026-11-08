#!/usr/bin/env python3
"""Stage 2: probe api/detail.json for an official subtitle track.

Why this exists
---------------
When the ASR tool is declined, the tempting assumption is "maybe Douyin has
official subtitles in the detail payload".  Guessing either way is bad: it can
produce a fake transcript, or leave a real one undiscovered.

This script turns that guess into an audited probe.  It walks the whole
aweme_detail (including JSON-encoded string payloads, which Douyin uses for
extras), records every candidate field checked, and states clearly whether a
genuine subtitle track exists.

Important: `desc` / `caption` are the AUTHOR'S POST TEXT, not subtitles.  They
are reported but hard-marked `is_transcript=false` so nobody can launder a
title into a verbatim source.

Usage:
    python probe_subtitles.py --workdir "$DOUYIN_DISTILL_ROOT/<id>"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dy_http import merge_status  # noqa: E402

SUBTITLE_KEYS = re.compile(
    r"subtitle|caption_info|cla_info|word_list|asr_|speech_to_text|"
    r"transcript|lyric|srt|vtt|ocr_text", re.I)
# Explicitly NOT subtitles: author-written post text and search suggestions.
AUTHOR_TEXT_KEYS = re.compile(r"^(desc|caption|seo_info|suggest_words|video_text)$", re.I)
CANDIDATE_PATHS = (
    "aweme_detail.video.subtitle_info",
    "aweme_detail.video.caption_info",
    "aweme_detail.video.cla_info",
    "aweme_detail.subtitle_info",
    "aweme_detail.caption_info",
    "aweme_detail.video_text",
    "aweme_detail.seo_info",
    "aweme_detail.douyin_p_c_video_extra",
    "aweme_detail.douyin_pc_video_extra_seo",
    "aweme_detail.video.big_thumbs",
)


def maybe_json(value, depth=0):
    if depth > 3:
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith(("{", "[")):
            try:
                return maybe_json(json.loads(s), depth + 1)
            except (ValueError, TypeError):
                return value
    return value


def dig(root, dotted):
    node = root
    for seg in dotted.split("."):
        node = maybe_json(node)
        if isinstance(node, dict):
            node = node.get(seg)
        elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
            node = node[int(seg)]
        else:
            return None
    return maybe_json(node)


def looks_like_subtitle(value) -> bool:
    """A real subtitle track is a list of timed segments or a URL to one."""
    value = maybe_json(value)
    if isinstance(value, dict):
        urls = [str(v) for v in value.values()
                if isinstance(v, str) and re.search(r"\.(srt|vtt|json|ass)(\?|$)", v, re.I)]
        if urls:
            return True
        for key in ("subtitle_list", "segments", "list", "words", "captions"):
            if key in value and looks_like_subtitle(value[key]):
                return True
        return False
    if isinstance(value, list):
        if not value:
            return False
        timed = 0
        for item in value:
            item = maybe_json(item)
            if isinstance(item, dict) and any(
                    k in item for k in ("start", "start_time", "begin", "timestamp", "text", "word")):
                timed += 1
        return timed >= 2
    if isinstance(value, str):
        return bool(re.search(r"https?://\S+\.(srt|vtt|ass)(\?|$)", value, re.I))
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--detail", default="")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    (workdir / "source").mkdir(parents=True, exist_ok=True)
    detail = Path(args.detail) if args.detail else workdir / "api" / "detail.json"
    status_file = workdir / "status.json"

    if not detail.is_file():
        result = {"ok": False, "error": "detail_json_missing", "expected": str(detail),
                  "checked_paths": []}
    else:
        payload = json.loads(detail.read_text(encoding="utf-8"))
        audit = []
        found_path, found_value = "", None
        for path in CANDIDATE_PATHS:
            value = dig(payload, path)
            if value is None:
                audit.append({"path": path, "state": "missing"})
            elif value == [] or value == {} or value == "":
                audit.append({"path": path, "state": "empty"})
            elif AUTHOR_TEXT_KEYS.search(path.rsplit(".", 1)[-1]):
                audit.append({"path": path, "state": "author_text_not_subtitle",
                              "preview": json.dumps(value, ensure_ascii=False)[:120]})
            elif looks_like_subtitle(value):
                audit.append({"path": path, "state": "found"})
                found_path, found_value = path, value
                break
            else:
                audit.append({"path": path, "state": "non_subtitle_payload",
                              "preview": json.dumps(value, ensure_ascii=False)[:120]})

        # Recursive sweep: catch subtitle-shaped keys we did not list.
        if not found_path:
            def walk(node, prefix="", depth=0):
                nonlocal found_path, found_value
                if depth > 6 or found_path:
                    return
                node = maybe_json(node)
                if isinstance(node, dict):
                    for k, v in node.items():
                        p = f"{prefix}.{k}" if prefix else k
                        if SUBTITLE_KEYS.search(k) and not AUTHOR_TEXT_KEYS.search(k):
                            vv = maybe_json(v)
                            state = "found" if looks_like_subtitle(vv) else "empty_or_unusable"
                            audit.append({"path": p, "state": state, "discovered_by": "sweep"})
                            if state == "found":
                                found_path, found_value = p, vv
                                return
                        walk(v, p, depth + 1)
                elif isinstance(node, list) and depth < 6:
                    for i, v in enumerate(node[:20]):
                        walk(v, f"{prefix}[{i}]", depth + 1)
            walk(payload.get("aweme_detail", {}), "aweme_detail")

        result = {
            "ok": bool(found_path),
            "source_path": found_path,
            "subtitle_count": (len(found_value) if isinstance(found_value, list) else 1)
                              if found_path else 0,
            "checked_paths": audit,
            "detail_file": str(detail),
            "author_text_policy": "desc/caption are post text, never a verbatim transcript",
        }
        if found_path:
            out = workdir / "source" / "subtitles.json"
            out.write_text(json.dumps({"source_path": found_path, "data": found_value},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            result["written_to"] = str(out)

    (workdir / "source" / "subtitle-probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    merge_status(status_file, subtitles=result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
