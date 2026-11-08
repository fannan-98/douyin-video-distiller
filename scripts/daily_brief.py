#!/usr/bin/env python3
"""Morning brief skeleton: what came in overnight, and is any of it useful.

The 09:30 half of the loop.  It does not write the note — that stays with the
model — but it removes every mechanical step around it: gathering what was
collected, pulling the numbers that decide whether a video is worth distilling
at all, and leaving an explicit slot for the judgement the model must make.

The judgement slot matters.  A brief that only lists titles invites "copy the
summary" work; a brief that asks *"这条对我们有没有可用点，依据是哪一段"* forces the
answer to be falsifiable.  What counts as 可用点 is **not** decided here — it is
read from ``_watch/profile.md``, which the user owns.  An empty profile means the
morning run must ask instead of inventing a business direction.

Usage
-----
    python daily_brief.py                 # 今天
    python daily_brief.py --date 2026-11-08
    python daily_brief.py --open          # 顺便打印未完成项
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

WATCH_ROOT = Path("D:/douyin-distill/_watch")
BATCH_DIR = WATCH_ROOT / "batches"
BRIEF_DIR = WATCH_ROOT / "brief"


def read_json(path: Path, default=None):
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {} if default is None else default


def fmt_dur(ms: int) -> str:
    if not ms:
        return "未知"
    s = ms / 1000
    return f"{int(s // 60)} 分 {int(s % 60)} 秒"


def fmt_time(ts: int) -> str:
    if not ts:
        return "未知"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def transcript_peek(path: Path, lines: int = 6) -> list[str]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("["):
            out.append(line)
        if len(out) >= lines:
            break
    return out


def profile_ready() -> tuple[bool, str]:
    """Is there a user-defined judgement basis? Empty means: ask, don't assume."""
    import re  # noqa: PLC0415

    path = WATCH_ROOT / "profile.md"
    if not path.is_file():
        return False, f"{path} 不存在"
    text = path.read_text(encoding="utf-8", errors="replace")
    # Strip whole <!-- ... --> blocks (they span lines and hold the examples),
    # then drop headings, blockquotes and blank lines. Anything left is real.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    body = [ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith(("#", ">"))]
    if not body:
        return False, f"{path} 只有模板没有内容"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成晨报底稿")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    from watch import QUEUE, load  # noqa: PLC0415

    batch = read_json(BATCH_DIR / f"{args.date}.json", {})
    results = batch.get("results", [])
    prepared = [r for r in results if r.get("outcome") in ("collected", "prepared")]
    failed = [r for r in results if r.get("outcome") in ("failed", "deferred")]

    queue_items = load(QUEUE, "items")["items"]
    backlog = [i for i in queue_items if i["status"] in ("queued", "processing")]
    stuck = [i for i in queue_items if i["status"] == "failed"]
    awaiting = [i for i in queue_items if i["status"] == "collected"]
    # Notes uploaded to ima and edited afterwards.  They stay `distilled`
    # because the remote copy no longer matches, and a brief that hides this
    # would report "已入库" for a version nobody can open.
    drift = [i for i in queue_items if i.get("note_drift")]

    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M")
    out_path = BRIEF_DIR / f"{args.date}-{stamp}.md"

    lines: list[str] = []
    add = lines.append
    add(f"# 凌晨采集晨报 · {args.date}")
    add("")
    add(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} · "
        f"扫描来源：`D:/douyin-distill/_watch/`")
    add(f"> 本次采集 {batch.get('requested', 0)} 条，成功 {len(prepared)} 条，"
        f"失败/顺延 {len(failed)} 条，其中 {batch.get('with_transcript', 0)} 条拿到逐字稿。")
    add(f"> 队列现状：待采集 {len(backlog)} 条 · **待蒸馏 {len(awaiting)} 条** · "
        f"失败待处理 {len(stuck)} 条"
        + (f" · **笔记已漂移 {len(drift)} 条**" if drift else "") + "。")
    ready, why = profile_ready()
    if not ready:
        add(">")
        add(f"> ⚠️ **判断基准未填写**（{why}）。下面只列事实，不做取舍；"
            "填好 `_watch/profile.md` 之前，不要替我假设业务方向。")
    add("")
    add("## 一、昨夜清单")
    add("")
    if prepared:
        add("| # | 视频 | 时长 | 章节 | 逐字稿 | 交付等级 |")
        add("| --- | --- | --- | --- | --- | --- |")
        for i, row in enumerate(prepared, 1):
            rd = Path(row.get("run_dir", ""))
            detail = read_json(rd / "api" / "detail.json", {})
            aweme = detail.get("aweme_detail") or detail or {}
            desc = str(aweme.get("desc", "")).split("\n")[0][:38]
            add(f"| {i} | [{desc or row['aweme_id']}]"
                f"(https://www.douyin.com/video/{row['aweme_id']}) | "
                f"{fmt_dur(aweme.get('duration', 0))} | "
                f"{row.get('chapter_count', 0)} | "
                f"{'有' if row.get('transcript') else '无'}"
                f"（{row.get('asr_text_segments', 0)}/{row.get('asr_segments', 0)} 段） | "
                f"`{row.get('deliverable', '')}` |")
    else:
        add("_没有成功采集的视频。_")
    add("")

    if failed:
        add("### 未完成")
        add("")
        for row in failed:
            add(f"- `{row['aweme_id']}` · {row.get('outcome')} · "
                f"{row.get('reason', '')[:120]}")
        add("")

    add("## 二、逐条速览")
    add("")
    for i, row in enumerate(prepared, 1):
        rd = Path(row.get("run_dir", ""))
        detail = read_json(rd / "api" / "detail.json", {})
        aweme = detail.get("aweme_detail") or detail or {}
        author = (aweme.get("author") or {}).get("nickname", "")
        chapters = read_json(rd / "source" / "chapters.json", {})
        add(f"### {i}. {str(aweme.get('desc', row['aweme_id'])).splitlines()[0][:60]}")
        add("")
        add(f"- 链接：https://www.douyin.com/video/{row['aweme_id']}")
        add(f"- 作者 / 发布：{author or '未知'} · {fmt_time(aweme.get('create_time', 0))}")
        add(f"- 时长 / 章节：{fmt_dur(aweme.get('duration', 0))} / "
            f"{chapters.get('count', 0)} 条官方章节")
        add(f"- 交付等级：`{row.get('deliverable', '')}`"
            f"（{'逐字稿型' if row.get('transcript') else '章节型'}）")
        add(f"- 运行目录：`{rd}`")
        items = chapters.get("chapters") or chapters.get("items") or []
        if items:
            add("")
            add("**官方章节**")
            add("")
            for ch in items[:12]:
                # extract_chapters.py 产出的是 time_ms + time_label，
                # 不是 timestamp；早期版本写成 timestamp 会全部显示 00:00.
                label = ch.get("time_label") or ""
                if not label:
                    ms = ch.get("time_ms") or ch.get("timestamp") or 0
                    sec = int(ms) // 1000
                    label = f"{sec // 60:02d}:{sec % 60:02d}"
                add(f"- `{label}` {ch.get('title', '')}")
        peek = transcript_peek(rd / "source" / "transcript.md")
        if peek:
            add("")
            add("**逐字稿开头**")
            add("")
            for line in peek:
                add(f"> {line[:150]}")
        add("")
        add("**这条对我们有什么可用点**（待填，必须给依据）")
        add("")
        add("- 可用点：")
        add("- 依据（时间点 / 章节）：")
        add("- 建议动作：蒸馏入库 / 只做索引 / 归档不处理")
        add("")

    add("## 三、队列实况（晨报不能只看昨夜成功的那几条）")
    add("")
    if awaiting:
        add(f"**待蒸馏 {len(awaiting)} 条**——逐字稿已就绪，还欠笔记：")
        add("")
        for it in awaiting:
            add(f"- `{it['aweme_id']}` · {it.get('title_hint') or '（无标题记录）'}"
                f" · {it.get('run_dir', '')}")
        add("")
    if backlog:
        add(f"**待采集 {len(backlog)} 条**（时间预算或上限用尽，留到下一轮）：")
        add("")
        for it in backlog[:20]:
            add(f"- `{it['aweme_id']}` · {it.get('title_hint') or '（无标题记录）'}"
                f" · 已尝试 {it.get('attempt', 0)} 次")
        add("")
    if stuck:
        add(f"**失败待处理 {len(stuck)} 条**：")
        add("")
        for it in stuck:
            add(f"- `{it['aweme_id']}` · {it.get('error_class', '?')} · "
                f"第 {it.get('attempt', 0)} 次 · {(it.get('last_error') or '')[:120]}")
        add("")
    if drift:
        add(f"**已入库但本地笔记被改过 {len(drift)} 条**"
            f"（ima 里存的是旧版本，需重新 finalize 才算入库）：")
        add("")
        for it in drift:
            add(f"- `{it['aweme_id']}` · {(it.get('note_drift') or '')[:170]}")
        add("")
    if not (awaiting or backlog or stuck or drift):
        add("_队列为空。_")
        add("")

    add("## 四、下一步")
    add("")
    add("1. 逐条判断是否值得蒸馏，不值得的把 `watch.py mark <id> --status skipped` 记下理由。")
    add("2. 值得的：`run_pipeline.py check --run-dir <R>` → 上传 → `finalize --receipt`。")
    add("3. 本次记录见 `batches/" + args.date + ".json`。")
    add("")

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    # A stable name for automation to point at; the dated file is the history.
    (BRIEF_DIR / "latest.md").write_text(text, encoding="utf-8")
    print(json.dumps({"ok": True, "brief": str(out_path),
                      "latest": str(BRIEF_DIR / "latest.md"),
                      "collected": len(prepared), "failed": len(failed),
                      "with_transcript": sum(1 for r in prepared if r.get("transcript")),
                      "backlog": len(backlog), "awaiting_distillation": len(awaiting),
                      "failed_items": len(stuck),
                      "note_drift": len(drift),
                      "profile_ready": ready,
                      "profile_hint": why if not ready else "",
                      "next": ("填 _watch/profile.md 后再做取舍，不要自行假设业务方向"
                               if not ready else "按 profile.md 逐条判断")},
                     ensure_ascii=False, indent=2))
    if args.open:
        print(out_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
