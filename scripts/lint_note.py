#!/usr/bin/env python3
"""Pre-upload lint for a 抖音视频精选 note.

Why this exists
---------------
Three classes of defects were actually shipped before this script:

1. Two rounds of notes were described as "缺字稿 / 有限笔记 / 缺口" even though
   the deliverable was a complete note backed by official chapters + author
   copy.  Evidence *level* is not a defect.
2. A chapter timestamp copied from another video ("00:02" for a chapter whose
   timestamp is 0), stale interaction numbers, and 【平台摘要】 marks leaking
   into 核心要点 (where only 【归纳】/【文案】/【推断】 are allowed).
3. Template placeholders and inconsistent section names.

All of those were written into prose rules that the model failed to follow.
This script turns them into hard, machine-checkable gates.  Run it BEFORE
阶段 4 (create_media).  Exit code 1 means "do not upload".

Usage
-----
    python scripts/lint_note.py --note "<note.md>" --workdir "<workdir>"
    python scripts/lint_note.py --note "<note.md>" --json-out "<lint.json>"

`--workdir` lets the linter read `source/chapters.json` and
`source/transcription-gate.json`.  Without it, structural checks still run but
chapter/timestamp cross-checks are skipped (reported as a warning).
"""

import argparse
import json
import re
import sys
from pathlib import Path

CN_NUM = "一二三四五六七八九"
REQUIRED_SECTIONS = [
    "一句话主旨", "内容元信息", "官方章节", "核心要点",
    "可执行清单", "边界与风险",
    # 这一节曾硬编码为「与业务的关联」，模板里还写死成「（家纺四件套 / 抖店）」，
    # 等于假定每位使用者的类目都一样。蒸馏一条视频不该替使用者决定他的场景是什么，
    # 所以给一组中立叫法；旧标题仍保留在别名里，否则历史笔记会集体 lint 失败。
    ("与业务的关联", "可迁移点", "与我的场景的关联", "对本场景的用处", "适用场景"),
    "来源与证据", "旁证",
]

# Terms that must never appear in a delivered 精选笔记.  "未取得" is NOT in
# this list: audited missing fields belong in 内容元信息.
SELF_DEPRECATING = [
    "未完成", "缺口", "有限笔记", "有限模式", "不覆盖视频全部内容",
    "章节要点未取得", "缺字稿", "半成品", "残次品", "蒸馏未完成",
]

# 章节型笔记禁止【原文】；full_note 允许.  【平台摘要】只允许第三节.
VALID_MARKS = frozenset({"平台摘要", "文案", "归纳", "推断", "原文"})

RE_TABLE_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(\d{1,2}:\d{2})\s*\|\s*([^|]+?)\s*\|")
RE_DETAIL_HEAD = re.compile(r"^####\s*(\d+)\.\s*(.+?)\s*·\s*(\d{1,2}:\d{2})\s*$")
RE_SECTION = re.compile(r"^##\s*([一二三四五六七八九])、\s*(.+)$")
RE_MARK = re.compile(r"【([^】]{1,20})】")
RE_INLINE_CODE = re.compile(r"`[^`]*`")
RE_PLACEHOLDER = re.compile(r"\{\{|\}\}")


def strip_code(line: str) -> str:
    """Inline code is documentation ABOUT a mark, not a claim using it."""
    return RE_INLINE_CODE.sub("", line)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint a 抖音精选笔记 before upload")
    ap.add_argument("--note", required=True, help="path to the markdown note")
    ap.add_argument("--workdir", default="", help="workdir containing source/chapters.json")
    ap.add_argument("--json-out", default="", help="optional path to write the JSON report")
    ap.add_argument("--deliverable", default="",
                     help="override gate deliverable (full_note/limited_note/...)")
    args = ap.parse_args()

    note_path = Path(args.note)
    if not note_path.is_file():
        print(json.dumps({"ok": False, "error": "note_not_found", "path": str(note_path)},
                         ensure_ascii=False, indent=2))
        return 2
    text = note_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    errors, warnings, checks = [], [], {}

    def err(code, detail, line=0):
        errors.append({"code": code, "line": line, "detail": detail})

    def warn(code, detail, line=0):
        warnings.append({"code": code, "line": line, "detail": detail})

    # --- 1. title / placeholders / self-deprecating language --------------
    title = next((l for l in lines if l.startswith("# ")), "")
    checks["title"] = title.lstrip("# ").strip()
    if "素材卡" in title or "蒸馏未完成" in title:
        err("title_is_source_card", "标题是素材卡/未完成，禁止作为精选笔记入库")
    if "精选笔记" not in title:
        warn("title_missing_suffix", "标题建议以「· 精选笔记」结尾")

    for i, line in enumerate(lines, 1):
        if RE_PLACEHOLDER.search(line):
            err("placeholder_leftover", f"残留模板占位符：{line.strip()[:80]}", i)
        # 模板里 `{{字段路径}}` 被静默替换成空串，会留下「来自 （…）」这种
        # 看似填了、实际没有出处的句子（知识库里已有一份旧笔记中招）.
        if re.search(r"来自\s*[（(]|来自\s*`\s*`|共\s*`\s*`\s*个", line):
            err("empty_source_path",
                f"章节来源字段路径为空：{line.strip()[:80]}", i)
        plain = strip_code(line)
        for term in SELF_DEPRECATING:
            if term in plain:
                # Legacy section heading is a naming issue, not a banner.
                if term == "缺口" and re.match(r"^##\s*[八8][、.]\s*来源与缺口", line):
                    err("legacy_section_name", "节名「来源与缺口」必须改为「来源与证据」", i)
                    continue
                err("self_deprecating_language",
                    f"出现自贬表述「{term}」：{line.strip()[:80]}", i)

    # --- 2. sections ------------------------------------------------------
    sections = []
    for i, line in enumerate(lines, 1):
        m = RE_SECTION.match(line)
        if m:
            sections.append((i, m.group(1), m.group(2).strip()))
    checks["sections_found"] = len(sections)
    found_names = [s[2] for s in sections]
    for need in REQUIRED_SECTIONS:
        names = need if isinstance(need, tuple) else (need,)
        if not any(n in name for n in names for name in found_names):
            err("section_missing", f"缺少必需小节「{names[0]}」")
    if len(sections) != 9:
        warn("section_count", f"期望 9 个二级小节，实际 {len(sections)} 个")

    # Section 3 line range (for 【平台摘要】 scope check).
    # 第三节可能叫「官方章节要点…」也可能叫「课程地图：N 个官方章节」；
    # 用序号「三」定位最稳，避免把第三节内合法的【平台摘要】全判成越界.
    sec3_start = next((s[0] for s in sections if s[1] == "三"), None)
    if sec3_start is None:
        sec3_start = next((s[0] for s in sections if "官方章节" in s[2]), None)
    next_after_3 = next((s[0] for s in sections if s[0] > (sec3_start or 0)), len(lines) + 1)

    # --- 3. evidence marks -------------------------------------------------
    gate = None
    if args.workdir:
        gate = load_json(Path(args.workdir) / "source" / "transcription-gate.json")
    deliverable = args.deliverable or (gate or {}).get("deliverable", "")
    checks["deliverable"] = deliverable or "unknown"
    if not deliverable:
        warn("gate_unavailable", "未读到 transcription-gate.json，跳过章节型专属规则")

    mark_counts = {}
    for i, line in enumerate(lines, 1):
        plain = strip_code(line)
        for mark in RE_MARK.findall(plain):
            mark_counts[mark] = mark_counts.get(mark, 0) + 1
            if mark not in VALID_MARKS:
                err("unknown_evidence_mark", f"未定义的证据标记【{mark}】", i)
            elif mark == "平台摘要" and not (sec3_start and sec3_start <= i < next_after_3):
                err("platform_summary_outside_section3",
                    f"【平台摘要】只允许出现在第三节，出现在第 {i} 行", i)
            elif mark == "原文" and deliverable == "limited_note":
                err("verbatim_mark_in_chapter_note",
                    f"章节型笔记不得使用【原文】（第 {i} 行）", i)
    checks["evidence_marks"] = mark_counts
    if "平台摘要" not in mark_counts:
        warn("no_platform_summary", "未出现【平台摘要】，检查第三节是否引用了章节原文")

    # --- 4. chapters cross-check ------------------------------------------
    chapters_json = None
    if args.workdir:
        chapters_json = load_json(Path(args.workdir) / "source" / "chapters.json")
    expected = (chapters_json or {}).get("chapters", []) if chapters_json else []

    table_rows, detail_heads = [], []
    in_table = False
    for i, line in enumerate(lines, 1):
        # 旧版笔记用「### 3.2 章节列表」，新版用「### 章节列表」，都要认.
        if re.match(r"^###\s+(?:\d+(?:\.\d+)*\s+)?章节列表\s*$", line):
            in_table = True
            continue
        if in_table and line.startswith("### "):
            in_table = False
        if in_table:
            m = RE_TABLE_ROW.match(line)
            if m:
                table_rows.append((i, int(m.group(1)), m.group(2), m.group(3).strip()))
        m2 = RE_DETAIL_HEAD.match(line)
        if m2:
            detail_heads.append((i, int(m2.group(1)), m2.group(3), m2.group(2).strip()))

    checks["chapters_json_count"] = len(expected)
    checks["chapters_table_rows"] = len(table_rows)
    checks["chapters_detail_blocks"] = len(detail_heads)

    # "共 N 个" in the header block / metadata must match chapters.json.
    declared = re.findall(r"共\s*(\d+)\s*个(?:章节)?", text)
    checks["declared_counts"] = declared
    if expected:
        n = len(expected)
        if any(int(d) != n for d in declared):
            err("chapter_count_mismatch", f"笔记写的章节数与 chapters.json 的 {n} 条不一致：{declared}")
        if len(table_rows) != n:
            err("chapter_table_count", f"章节列表 {len(table_rows)} 行，应为 {n} 行")
        if len(detail_heads) != n:
            err("chapter_detail_count", f"逐条原文 {len(detail_heads)} 条，应为 {n} 条")

        by_index = {int(c.get("index", j + 1)): c for j, c in enumerate(expected)}
        for source, rows in (("章节表", table_rows), ("逐条原文", detail_heads)):
            for line_no, idx, label, label_title in rows:
                c = by_index.get(idx)
                if not c:
                    err("chapter_index_out_of_range", f"{source} 第 {idx} 条超出 chapters.json 范围", line_no)
                    continue
                want = str(c.get("time_label", "")).lstrip("0") or "0"
                got = label.lstrip("0") or "0"
                if want != got:
                    err("chapter_timestamp_mismatch",
                        f"{source} 第 {idx} 条时间点 {label} 与 chapters.json 的 {c.get('time_label')} 不符",
                        line_no)
                # 逐条原文的标题紧跟在 "N. " 后面；章节表第三列也是标题.
                if source == "逐条原文" and label_title != str(c.get("title", "")).strip():
                    err("chapter_title_mismatch",
                        f"第 {idx} 条标题「{label_title}」与 chapters.json 的「{c.get('title')}」不符",
                        line_no)
    else:
        warn("chapters_json_unavailable", "未读到 chapters.json，跳过章节数 / 时间点交叉核对")

    # --- 5. canonical url --------------------------------------------------
    m = re.search(r"https://www\.douyin\.com/video/(\d{15,25})", text)
    checks["canonical_video_id"] = m.group(1) if m else ""
    if not m:
        err("missing_canonical_url", "缺少规范链接 https://www.douyin.com/video/<id>")
    elif args.workdir:
        status = load_json(Path(args.workdir) / "status.json") or {}
        vid = str(status.get("video_id", ""))
        if vid and vid != m.group(1):
            err("video_id_mismatch", f"笔记 video_id={m.group(1)}，status.json video_id={vid}")

    result = {
        "ok": not errors,
        "note": str(note_path),
        "title": checks["title"],
        "deliverable": deliverable or "unknown",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "gate": "BLOCK UPLOAD" if errors else ("PASS (with warnings)" if warnings else "PASS"),
    }
    out = json.dumps(result, ensure_ascii=False, indent=2)
    print(out)
    if args.json_out:
        Path(args.json_out).write_text(out + "\n", encoding="utf-8")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
