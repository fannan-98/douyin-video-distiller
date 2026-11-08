#!/usr/bin/env python3
"""Batch runner: turn the watch queue into downloaded + transcribed run dirs.

This is the 02:30 half of the loop.  It does only deterministic work — no note
writing, no upload — so a nightly batch can finish unattended.  It stops at
``collected``: the transcript is ready, the note is still owed to the morning
run.  v1.9 marked those `done`, and one real batch left 3 of 4 `done` items
with no note at all; the ledger claimed work that had never happened.

Failure handling follows Celery's autoretry recipe rather than "mark it failed
and move on":

* classify the error first — a deleted video is not retried, a timeout is;
* back off between attempts (60 s, 180 s) instead of hammering;
* stop at ``--max-attempts`` and leave a reason a human can act on.

`watch.py reconcile` runs first so orphans from a killed previous run are
reclaimed before new work is picked up.

Usage
-----
    python run_batch.py                      # 跑队列，新内容优先
    python run_batch.py --limit 3
    python run_batch.py --order oldest       # 清积压时用
    python run_batch.py --dry-run
    python run_batch.py --no-asr             # 只到章节型，省时间
    python run_batch.py --budget-minutes 60
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from watch import (  # noqa: E402
    MAX_ATTEMPTS, QUEUE, classify_error, load,
)

BATCH_DIR = Path("D:/douyin-distill/_watch/batches")
WATCH = SCRIPTS / "watch.py"


def run(cmd: list[str], timeout: int = 1800) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def mark(vid: str, status: str, **fields) -> None:
    cmd = [sys.executable, str(WATCH), "mark", vid, "--status", status]
    for key, value in fields.items():
        if value:
            cmd += [f"--{key.replace('_', '-')}", str(value)]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                   errors="replace")


def error_text(out: str, err: str) -> str:
    """Pull the real reason out of run_pipeline's JSON instead of its last line.

    v1.9 did ``(err or out).strip().splitlines()[-1]``, which for a JSON payload
    is the closing ``}`` — the ledger recorded `reason = "}"` for a genuine
    failure, i.e. no diagnosis at all.
    """
    for blob in (out, err):
        text = (blob or "").strip()
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = {}
            for key in ("error", "detail", "stderr_excerpt", "stdout_excerpt"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:300]
                if isinstance(value, dict):
                    inner = value.get("stderr_excerpt") or value.get("stdout_excerpt")
                    if inner:
                        return str(inner).strip()[:300]
    for blob in (err, out):
        lines = [ln.strip() for ln in (blob or "").splitlines() if ln.strip()]
        for line in reversed(lines):
            if line not in ("}", "}", "]", "{"):
                return line[:300]
    return "unknown"


def read_json(path: Path, default=None):
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {} if default is None else default


def attempt_once(vid: str, args) -> dict:
    cmd = [sys.executable, str(SCRIPTS / "run_pipeline.py"), "prepare",
           f"https://www.douyin.com/video/{vid}"]
    if not args.no_asr:
        cmd += ["--use-local-asr", "--asr-root", args.asr_root]
    if args.no_download:
        cmd.append("--no-download")
    t0 = time.time()
    try:
        code, out, err = run(cmd)
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": 124, "error": "timeout", "elapsed_s": None,
                "retryable": True}
    elapsed = round(time.time() - t0, 1)
    payload = {}
    if out.strip().startswith("{"):
        try:
            payload = json.loads(out)
        except ValueError:
            payload = {}
    if code == 0 and payload.get("ok"):
        return {"ok": True, "exit": 0, "elapsed_s": elapsed, "payload": payload}
    reason = error_text(out, err)
    return {"ok": False, "exit": code, "error": reason, "elapsed_s": elapsed,
            "retryable": classify_error(reason) == "retryable"}


def main() -> int:
    ap = argparse.ArgumentParser(description="批量采集队列")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-asr", action="store_true", help="跳过本地离线转写")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--gap", type=int, default=20, help="每条之间的间隔秒数")
    ap.add_argument("--budget-minutes", type=int, default=90)
    ap.add_argument("--order", choices=["newest", "oldest"], default="newest",
                    help="newest 优先新内容；oldest 用于清积压")
    ap.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    ap.add_argument("--retry-delay", type=int, default=60,
                    help="首次重试等待秒数，之后翻三倍")
    ap.add_argument("--no-reconcile", action="store_true")
    ap.add_argument("--json-out", default="",
                    help="把结果写成单个 JSON 文档（默认同时写 batches/<日期>.json）")
    ap.add_argument("--asr-root", default="D:/WorkBuddy-ASR")
    args = ap.parse_args()

    repaired = {}
    if not args.no_reconcile and not args.dry_run:
        code, out, _ = run([sys.executable, str(WATCH), "reconcile"])
        if code == 0 and out.strip().startswith("{"):
            try:
                repaired = json.loads(out)
            except ValueError:
                repaired = {}

    queue = load(QUEUE, "items")
    pending = [i for i in queue["items"] if i["status"] == "queued"]
    pending.sort(key=lambda i: i.get("first_seen", ""),
                 reverse=(args.order == "newest"))
    if args.limit:
        pending = pending[: args.limit]

    started = time.time()
    deadline = started + args.budget_minutes * 60
    results = []
    print(json.dumps({"ok": True, "stage": "batch_start", "pending": len(pending),
                      "order": args.order, "dry_run": args.dry_run,
                      "reclaimed": len(repaired.get("changes", [])),
                      "budget_minutes": args.budget_minutes},
                     ensure_ascii=False), flush=True)

    for index, item in enumerate(pending, 1):
        vid = item["aweme_id"]
        if time.time() > deadline:
            results.append({"aweme_id": vid, "outcome": "deferred",
                            "reason": "budget exhausted; stays queued"})
            continue
        if args.dry_run:
            results.append({"aweme_id": vid, "outcome": "would_run",
                            "title": item.get("title_hint", ""),
                            "nickname": item.get("nickname", "")})
            continue

        mark(vid, "processing")
        tries = []
        row = {}
        for attempt in range(1, max(1, args.max_attempts) + 1):
            row = attempt_once(vid, args)
            tries.append({"attempt": attempt, "exit": row.get("exit"),
                          "ok": row.get("ok"),
                          "error": row.get("error", "")})
            if row["ok"]:
                break
            if not row.get("retryable") or attempt >= args.max_attempts:
                break
            if time.time() + args.retry_delay * (3 ** (attempt - 1)) > deadline:
                tries.append({"attempt": attempt + 1, "skipped": "budget"})
                break
            time.sleep(args.retry_delay * (3 ** (attempt - 1)))

        if row["ok"]:
            payload = row["payload"]
            run_dir = payload.get("run_dir", "")
            asr = read_json(Path(run_dir) / "source" / "local-asr.json", {}) if run_dir else {}
            result = {"aweme_id": vid, "outcome": "collected", "run_dir": run_dir,
                      "deliverable": payload.get("deliverable", "unknown"),
                      "chapter_count": payload.get("chapter_count", 0),
                      "transcript": bool(payload.get("sources", {}).get("transcript")),
                      "asr_segments": asr.get("segments", 0),
                      "asr_text_segments": asr.get("segments_with_text", 0),
                      "asr_elapsed_s": asr.get("elapsed_s", 0),
                      "attempts": len(tries), "elapsed_s": row["elapsed_s"]}
            # collected, not done: the note is still owed.
            mark(vid, "collected", run_dir=run_dir)
        else:
            reason = row.get("error", "unknown")
            result = {"aweme_id": vid, "outcome": "failed", "exit": row.get("exit"),
                      "reason": reason,
                      "error_class": classify_error(reason),
                      "attempts": len(tries),
                      "elapsed_s": row.get("elapsed_s")}
            mark(vid, "failed", reason=reason, run_dir=row.get("payload", {}).get("run_dir", ""))

        result["tries"] = tries
        results.append(result)
        print(json.dumps({"ok": True, "progress": f"{index}/{len(pending)}", **result},
                         ensure_ascii=False), flush=True)

        if index < len(pending):
            time.sleep(args.gap)

    summary = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "order": args.order,
        "requested": len(pending),
        "collected": sum(1 for r in results if r["outcome"] == "collected"),
        "failed": sum(1 for r in results if r["outcome"] == "failed"),
        "deferred": sum(1 for r in results if r["outcome"] == "deferred"),
        "with_transcript": sum(1 for r in results if r.get("transcript")),
        "reclaimed_stale": repaired.get("change_count", 0),
        "elapsed_s": round(time.time() - started, 1),
        "results": results,
    }
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.json_out) if args.json_out else (
        BATCH_DIR / f"{datetime.now():%Y-%m-%d}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if not args.json_out and out_path.is_file():
        # Second batch of the same day: archive the earlier one instead of
        # overwriting it, so the day's history stays auditable.
        try:
            stamp = datetime.fromtimestamp(
                out_path.stat().st_mtime).strftime("%H%M")
            archive = out_path.with_name(f"{out_path.stem}-{stamp}.json")
            if not archive.exists():
                out_path.replace(archive)
        except OSError:
            pass
    out_path.write_text(payload + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "stage": "batch_done", "batch_file": str(out_path),
                      **{k: v for k, v in summary.items() if k != "results"}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
