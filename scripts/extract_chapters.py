#!/usr/bin/env python3
"""Stage 2: extract the *real* chapter points from api/detail.json.

Why this exists (2026-11-08, user-reported defect)
--------------------------------------------------
Four consecutive runs reported "章节要点：未取得（chapter_list 为空）" while the
data was sitting in the very same aweme_detail the downloader had already saved.
The root cause was a single-key lookup:

    aweme_detail.chapter_list  -> null

Douyin's PC/Web chapter panel ("章节要点：共 N 个") is actually served from

    aweme_detail.recommend_chapter_info.recommend_chapter_list

with `chapter_abstract`, `desc`, `detail` and millisecond `timestamp`.  The
legacy `chapter_list` / `anchors` / `video_text` keys are usually null even when
the panel has content.  Looking at one of them and concluding "not available"
is a false negative, not a data gap.

This script therefore:
  * checks an ORDERED list of candidate paths, including keys nested inside
    JSON-encoded strings (Douyin often double-encodes extra payloads);
  * always writes an audit trail of every path checked and what was found;
  * emits source/chapters.md (human) + source/chapters.json (machine);
  * merges into the single authoritative status.json;
  * refuses to let the pipeline silently proceed when nothing is found:
    exit code 3 and chapters.md explicitly says "all candidates empty".

Usage:
    python extract_chapters.py --workdir "D:/douyin-distill/<id>/run6"
    python extract_chapters.py --detail "<path>/api/detail.json" --workdir "<dir>"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dy_http import merge_status  # noqa: E402

# Ordered: the one that actually works today goes FIRST.
CANDIDATE_PATHS = (
    "aweme_detail.recommend_chapter_info.recommend_chapter_list",
    "aweme_detail.chapter_list",
    "aweme_detail.chapter_info.chapter_list",
    "aweme_detail.anchors",
    "aweme_detail.anchors_info.anchors",
    "aweme_detail.video_text",
    "aweme_detail.recommend_chapter_info.chapters",
    "chapter_list",
    "aweme_list.0.chapter_list",
)

ABSTRACT_PATHS = (
    "aweme_detail.recommend_chapter_info.chapter_abstract",
    "aweme_detail.chapter_abstract",
    "aweme_detail.ai_summary",
)

TITLE_KEYS = ("desc", "title", "name", "chapter_name", "text", "label")
DETAIL_KEYS = ("detail", "abstract", "content", "chapter_desc", "summary", "desc")
TIME_KEYS = ("timestamp", "start_time", "start", "time", "offset", "begin")


def maybe_json(value):
    """Douyin sometimes stores objects as JSON strings; decode them transitively."""
    for _ in range(3):
        if isinstance(value, str):
            text = value.strip()
            if text.startswith(("{", "[")):
                try:
                    value = json.loads(text)
                except (ValueError, TypeError):
                    return value
            else:
                return value
        else:
            return value
    return value


def dig(root, dotted: str):
    """Walk a dotted path; numeric segments index into lists."""
    node = root
    for segment in dotted.split("."):
        node = maybe_json(node)
        if isinstance(node, dict):
            if segment not in node:
                return None
            node = node[segment]
        elif isinstance(node, (list, tuple)):
            if not segment.isdigit() or int(segment) >= len(node):
                return None
            node = node[int(segment)]
        else:
            return None
    return maybe_json(node)


def as_chapters(value) -> list[dict]:
    """Normalise any candidate payload into [{time_ms, title, detail}]."""
    value = maybe_json(value)
    if isinstance(value, dict):
        for key in ("recommend_chapter_list", "chapter_list", "chapters", "list"):
            if key in value:
                value = value[key]
                break
    if not isinstance(value, list):
        return []

    out = []
    for item in value:
        item = maybe_json(item)
        if not isinstance(item, dict):
            if isinstance(item, str) and item.strip():
                out.append({"time_ms": None, "title": "", "detail": item.strip()})
            continue
        title = next((str(item[k]).strip() for k in TITLE_KEYS
                      if isinstance(item.get(k), str) and item[k].strip()), "")
        detail = next((str(item[k]).strip() for k in DETAIL_KEYS
                       if isinstance(item.get(k), str) and item[k].strip()), "")
        # If `desc` served as the title, don't repeat it as the detail.
        if detail == title:
            detail = ""
        time_ms = None
        for key in TIME_KEYS:
            raw = item.get(key)
            if isinstance(raw, (int, float)) and raw >= 0:
                time_ms = int(raw)
                break
            if isinstance(raw, str) and raw.strip().isdigit():
                time_ms = int(raw.strip())
                break
        if not (title or detail):
            continue
        out.append({"time_ms": time_ms, "title": title, "detail": detail})
    return out


def timestamp_label(ms, unit: str) -> str:
    if not isinstance(ms, int):
        return "—"
    # Douyin PC/Web chapter timestamps are MILLISECONDS (2000 -> 0:02,
    # 335000 -> 5:35).  A naive "big number means ms" rule mis-renders the
    # first two chapters as 33:20 / 283:20, which was caught on the first run.
    seconds = ms // 1000 if unit == "ms" else ms
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def detect_unit(chapters: list[dict], payload: dict) -> str:
    """Decide ms vs seconds using the video duration as the sanity bound.

    Two failure modes we actually hit:
      * naive ">100000 means ms" turns 2000ms into 33:20;
      * naive "always ms" turns a seconds-based feed (2, 120) into 00:00.
    So: if interpreting as ms collapses the whole series into <1 second while
    interpreting as seconds gives a real spread that fits the video, use
    seconds.  Otherwise any value that cannot possibly be seconds forces ms.
    """
    values = [c["time_ms"] for c in chapters if isinstance(c.get("time_ms"), int)]
    if not values:
        return "ms"
    duration_ms = dig(payload, "aweme_detail.video.duration")
    if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
        duration_ms = dig(payload, "aweme_detail.duration") or 0
    duration_s = float(duration_ms) / 1000 if duration_ms > 10_000 else float(duration_ms)
    bound = duration_s * 1.5 if duration_s > 0 else float("inf")
    lo, hi = min(values), max(values)

    span_if_ms = (hi - lo) / 1000.0
    span_if_s = hi - lo
    if span_if_ms < 1 and span_if_s >= 1 and hi <= bound:
        return "s"
    if hi > max(bound if duration_s > 0 else 0, 600):
        return "ms"
    if duration_s > 0 and hi / 1000.0 > bound:
        return "s"
    return "ms"  # Douyin PC/Web's default for this endpoint


def find_chapters(payload: dict) -> tuple[list[dict], str, str, list[dict], str]:
    """Return (chapters, winning_path, abstract, audit, unit)."""
    audit = []
    for path in CANDIDATE_PATHS:
        value = dig(payload, path)
        if value is None:
            audit.append({"path": path, "state": "missing"})
            continue
        if value == [] or value == {}:
            audit.append({"path": path, "state": "empty"})
            continue
        chapters = as_chapters(value)
        if chapters:
            audit.append({"path": path, "state": "found", "count": len(chapters)})
            abstract = ""
            for apath in ABSTRACT_PATHS:
                candidate = dig(payload, apath)
                if isinstance(candidate, str) and candidate.strip():
                    abstract = candidate.strip()
                    break
            return chapters, path, abstract, audit, detect_unit(chapters, payload)
        audit.append({"path": path, "state": "non_chapter_payload",
                      "preview": json.dumps(value, ensure_ascii=False)[:160]})
    return [], "", "", audit, "ms"


def render_markdown(video_id: str, chapters: list[dict], winning_path: str,
                    abstract: str, audit: list[dict], detail_path: Path,
                    unit: str) -> str:
    lines = [
        f"# {video_id} · 官方章节要点",
        "",
        f"> 数据来源：`{detail_path}` → `aweme_detail`，字段路径 `{winning_path or '（全部候选为空）'}`。",
        "> 性质：抖音平台生成的 AI 章节摘要原文，**不是口播逐字稿**；引用时标 `【平台摘要】`。",
        f"> 抓取方式：Skill v1.4 `scripts/extract_chapters.py` 自动抽取；共 {len(chapters)} 条。",
        f"> 时间戳单位：{unit}（依据视频时长自动判定，避免出现 33:20 这类误读）。",
        "",
    ]
    if abstract:
        lines += ["## 官方总摘要（chapter_abstract）", "", f"> {abstract}", ""]
    if chapters:
        lines += [
            "## 章节列表",
            "",
            "| # | 时间点 | 章节标题 | 章节摘要原文 |",
            "| --- | --- | --- | --- |",
        ]
        for idx, ch in enumerate(chapters, 1):
            detail = ch["detail"].replace("|", "\\|").replace("\n", " ") or "（该章节无摘要正文）"
            title = (ch["title"] or "（无标题）").replace("|", "\\|")
            lines.append(f"| {idx} | {timestamp_label(ch['time_ms'], unit)} | {title} | {detail} |")
        lines += [
            "",
            "## 逐条原文（便于引用）",
            "",
        ]
        for idx, ch in enumerate(chapters, 1):
            label = timestamp_label(ch["time_ms"], unit)
            lines.append(f"### {idx}. {ch['title'] or '（无标题）'} · {label}")
            lines.append("")
            lines.append(f"【平台摘要】{ch['detail'] or '该章节仅有标题，无摘要正文。'}")
            lines.append("")
            lines.append(f"> 出处：`{winning_path}[{idx - 1}]`，timestamp={ch['time_ms']}（{label}）")
            lines.append("")
    else:
        lines += [
            "## ⚠️ 全部候选字段为空",
            "",
            "本次确实没有取得章节要点。但必须先核对下面的检查清单，",
            "**禁止只查 `chapter_list` 一个键就下「未取得」结论。**",
            "",
        ]
    lines += [
        "## 字段检查清单（防漏检）",
        "",
        "| 候选路径 | 结果 |",
        "| --- | --- |",
    ]
    for row in audit:
        state = row["state"]
        extra = f" · {row['count']} 条" if state == "found" else ""
        if state == "non_chapter_payload":
            extra = f" · {row.get('preview', '')}"
        lines.append(f"| `{row['path']}` | {state}{extra} |")
    lines += [
        "",
        "已知陷阱：`chapter_list` / `anchors` / `video_text` 经常为 `null` 或 `[]`，",
        "而页面「章节要点：共 N 个」来自 `recommend_chapter_info.recommend_chapter_list`。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--detail", default="", help="path to api/detail.json")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "source").mkdir(exist_ok=True)
    detail_path = Path(args.detail) if args.detail else workdir / "api" / "detail.json"
    status_file = workdir / "status.json"
    video_id = ""
    for candidate in (workdir.name, workdir.parent.name):
        if re.fullmatch(r"\d{15,25}", candidate):
            video_id = candidate
            break

    if not detail_path.is_file():
        result = {"ok": False, "error": "detail_json_missing",
                  "expected": str(detail_path),
                  "hint": "Run stage 1C douyin_detail.py first. Do NOT fall back "
                          "to guessing from the video title."}
        merge_status(status_file, status="chapters_missing", chapters=result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    payload = json.loads(detail_path.read_text(encoding="utf-8"))
    chapters, winning_path, abstract, audit, unit = find_chapters(payload)
    for idx, ch in enumerate(chapters):
        ch["time_label"] = timestamp_label(ch["time_ms"], unit)
        ch["index"] = idx + 1

    md_path = workdir / "source" / "chapters.md"
    json_path = workdir / "source" / "chapters.json"
    md_path.write_text(render_markdown(video_id, chapters, winning_path,
                                       abstract, audit, detail_path, unit), encoding="utf-8")
    machine = {
        "video_id": video_id,
        "ok": bool(chapters),
        "count": len(chapters),
        "source_path": winning_path,
        "timestamp_unit": unit,
        "abstract": abstract,
        "chapters": chapters,
        "checked_paths": audit,
        "detail_file": str(detail_path),
        "note": "platform AI chapter summary, not verbatim speech; cite as 【平台摘要】",
    }
    json_path.write_text(json.dumps(machine, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {"ok": bool(chapters), "count": len(chapters),
              "source_path": winning_path, "abstract_chars": len(abstract),
              "files": {"chapters_md": str(md_path), "chapters_json": str(json_path)},
              "checked_paths": audit}
    merge_status(status_file,
                 status="chapters_ready" if chapters else "chapters_missing",
                 chapters=result,
                 next_step=("Stage 3: include the chapter section in the note. "
                            "Do NOT write 章节要点 未取得 when count > 0."
                            if chapters else
                            "Stage 3: chapters genuinely absent; list 章节要点原文 "
                            "under 未取得字段 and keep the audit trail."))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if chapters else 3


if __name__ == "__main__":
    raise SystemExit(main())
