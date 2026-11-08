#!/usr/bin/env python3
"""One-command orchestration for 抖音视频精选 (stages 0 -> 5).

The note itself is still written by the model (that is the creative step).
This script owns everything around it and removes the hand-assembled shell
commands that caused real failures in earlier runs (``tee`` before ``mkdir``,
missing ``--normalize-only``, skipping the gate, uploading before lint).

Subcommands
-----------
  prepare   "<raw>"        stages 0, 1C, 2, 1E -> handoff.json
                           (add --use-local-asr for an offline verbatim transcript)
  check     --run-dir R    lint the finished note -> upload-manifest.json
  finalize  --run-dir R    write STATUS.md + status.json=completed after ima verify
  clean                    prune old run dirs (dry-run unless -f), see below

Typical full run
----------------
    python run_pipeline.py prepare "<整段分享口令>" --use-local-asr
    # model writes <run-dir>/【WorkBuddy-...】<id>-<slug>.md using handoff.json
    python run_pipeline.py check --run-dir "<run-dir>"
    # assistant calls create_media -> upload_presigned.py -> add_knowledge
    # -> get_knowledge_list (MCP tools cannot be called from a plain script)
    python run_pipeline.py finalize --run-dir "<run-dir>" --media-id "<id>"

Retention (borrowed from `nextflow clean`)
------------------------------------------
Every run keeps a full copy of the downloaded media (~107 MiB each), so a
handful of re-runs quietly eats a gigabyte.  `clean` follows the semantics of
`nextflow clean` — dry-run by default, ``-f`` to act, ``--keep-last N`` to keep
the most recent runs — with one deliberate difference: **a completed run is
never deleted, only its media is pruned**.  The note is already in ima, but
`status.json` / `STATUS.md` / `lint-note.json` are the only local audit trail of
what was published, so the directory itself has archival value.  Failed and
abandoned runs have no such value and are removed outright.

Everything is auditable: pipeline-steps.json records every command + exit code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dy_config import ASR_ROOT, DISTILL_ROOT, IMA_KB_ID

SKILL_DIR = Path(__file__).resolve().parent
SCRIPTS = SKILL_DIR
# Stage 4/5 need a real id; doctor.py checks this before you get there.
KB_ID = IMA_KB_ID or "YOUR_IMA_KB_ID"
VIDEO_ID_RE = re.compile(r"\d{15,25}")


def sha256_file(path: Path) -> str:
    """Content digest of a file, streamed so a big note never lands in RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default=None):
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {} if default is None else default


def run_step(steps: list, name: str, args: list[str], *, allow_fail: bool = False,
             ok_codes: tuple[int, ...] = (0,)):
    """Run one script. `ok_codes` covers scripts whose non-zero exit is a
    legitimate finding: probe_subtitles / extract_chapters return 3 for
    'not found', which must not abort the pipeline."""
    cmd = [sys.executable, str(SCRIPTS / args[0])] + args[1:]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    entry = {
        "step": name, "script": args[0], "args": args[1:],
        "exit_code": proc.returncode,
        "ok_codes": list(ok_codes),
        "stdout_excerpt": proc.stdout.strip()[:400],
        "stderr_excerpt": proc.stderr.strip()[:400],
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    steps.append(entry)
    if proc.returncode not in ok_codes and not allow_fail:
        raise PipelineError(name, entry)
    return entry, proc.stdout


class PipelineError(Exception):
    def __init__(self, step: str, entry: dict):
        self.step, self.entry = step, entry
        super().__init__(f"stage {step} failed (exit {entry['exit_code']}): "
                         f"{entry['stderr_excerpt'] or entry['stdout_excerpt']}")


def next_run_dir(video_id: str, explicit: str) -> Path:
    if explicit:
        return Path(explicit)
    base = DISTILL_ROOT / video_id
    used = [int(m.group(1)) for p in base.glob("run*")
            if p.is_dir() and (m := re.fullmatch(r"run(\d+)", p.name))]
    return base / f"run{max(used, default=0) + 1}"


def resolve_video_id(raw: str, steps: list) -> str:
    entry, out = run_step(steps, "resolve_input", ["resolve_input.py", raw])
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise PipelineError("resolve_input_parse", {
            "exit_code": 1, "stdout_excerpt": out[:300], "stderr_excerpt": str(exc)}) from exc
    if not data.get("ok") or not data.get("video_id"):
        raise PipelineError("resolve_input", {
            "exit_code": 1, "stdout_excerpt": out[:300], "stderr_excerpt": "no video_id"})
    return str(data["video_id"])


def find_note(run_dir: Path, explicit: str) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit(f"note not found: {p}")
        return p
    candidates = [p for p in run_dir.glob("*.md")
                  if "SOURCE-CARD" not in p.name and "素材卡" not in p.name
                  and p.name != "STATUS.md"]
    if not candidates:
        raise SystemExit("no note markdown in run dir; pass --note")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def cmd_prepare(args) -> int:
    steps: list[dict] = []
    video_id = resolve_video_id(args.raw, steps)
    run_dir = next_run_dir(video_id, args.run_dir)
    (run_dir / "api").mkdir(parents=True, exist_ok=True)
    (run_dir / "source").mkdir(parents=True, exist_ok=True)
    steps_path = run_dir / "pipeline-steps.json"

    def save(extra=None):
        payload = {"video_id": video_id, "run_dir": str(run_dir), "steps": steps}
        if extra:
            payload.update(extra)
        steps_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        return payload

    try:
        run_step(steps, "stage0_normalize",
                 ["douyin_fetch.py", args.raw, "--workdir", str(run_dir),
                  "--normalize-only", "--json-out", str(run_dir / "stage0.json")])
        save()

        if not args.no_download:
            run_step(steps, "stage1c_download",
                     ["douyin_detail.py", video_id, "--download",
                      "--out", str(run_dir / "api"), "--workdir", str(run_dir)])
            save()

        run_step(steps, "stage2_subtitles",
                 ["probe_subtitles.py", "--workdir", str(run_dir)], ok_codes=(0, 3))
        run_step(steps, "stage2_chapters",
                 ["extract_chapters.py", "--workdir", str(run_dir)], ok_codes=(0, 3))
        save()

        # --- stage 1B: optional local ASR -------------------------------------
        asr_report = None
        if args.use_local_asr:
            entry, out = run_step(
                steps, "stage1b_local_asr",
                ["local_asr.py", "--workdir", str(run_dir), "--asr-root", args.asr_root],
                allow_fail=True)
            asr_report = read_json(run_dir / "source" / "local-asr.json", {})
            if entry["exit_code"] != 0:
                steps.append({"step": "stage1b_fallback", "note":
                              "local ASR unavailable/failed; continue with chapters + author copy",
                              "detail": entry["stderr_excerpt"] or entry["stdout_excerpt"]})

        transcript_md = run_dir / "source" / "transcript.md"
        has_transcript = transcript_md.is_file()
        if has_transcript:
            from resolve_transcript import looks_like_transcript  # noqa: PLC0415
            has_transcript, _ = looks_like_transcript(
                transcript_md.read_text(encoding="utf-8"))

        if args.transcription_state:
            state, reason = args.transcription_state, args.reason or "explicit override"
        elif has_transcript:
            state = "done"
            reason = ("verbatim transcript present"
                      + (" (local offline ASR)" if asr_report else " (reused/pasted)"))
        else:
            state = "skipped"
            reason = ("pipeline did not call a cloud ASR tool; a complete note can be "
                      "written from official chapters + author copy. Add --use-local-asr "
                      "for an offline verbatim transcript.")
        run_step(steps, "stage1e_gate",
                 ["transcription_gate.py", "--workdir", str(run_dir),
                  "--state", state, "--reason", reason])
        save()

        status = read_json(run_dir / "status.json", {})
        gate = read_json(run_dir / "source" / "transcription-gate.json", {})
        chapters = read_json(run_dir / "source" / "chapters.json", {})
        handoff = {
            "video_id": video_id,
            "run_dir": str(run_dir),
            "canonical_url": f"https://www.douyin.com/video/{video_id}",
            "deliverable": gate.get("deliverable", "unknown"),
            "transcription_state": state,
            "chapter_count": chapters.get("count", 0),
            "chapter_source": chapters.get("source_path", ""),
            "note_template": str(SKILL_DIR.parent / "assets" / "note-template.md"),
            "distill_spec": str(SKILL_DIR.parent / "references" / "distill-spec.md"),
            "suggested_note_name": f"【WorkBuddy-{datetime.now():%Y-%m-%d}】{video_id}-<slug>.md",
            "sources": {
                "detail_json": str(run_dir / "api" / "detail.json"),
                "chapters_md": str(run_dir / "source" / "chapters.md"),
                "chapters_json": str(run_dir / "source" / "chapters.json"),
                "subtitle_probe": str(run_dir / "source" / "subtitle-probe.json"),
                "transcript": str(transcript_md) if has_transcript else "",
                "meta_hint": "write source/meta.md from detail.json",
            },
            "status_snapshot": {k: status.get(k) for k in
                                ("video_id", "status", "failure_class")},
            "next_action": ("Write the note in run_dir, then run: "
                            f'python run_pipeline.py check --run-dir "{run_dir}"'),
        }
        (run_dir / "handoff.json").write_text(
            json.dumps(handoff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        save({"handoff": handoff})
        print(json.dumps({"ok": True, "stage": "prepared", **handoff},
                         ensure_ascii=False, indent=2))
        return 0
    except PipelineError as exc:
        save({"error": str(exc), "failed_step": exc.step})
        print(json.dumps({"ok": False, "error": str(exc),
                          "steps_file": str(steps_path), "detail": exc.entry},
                         ensure_ascii=False, indent=2))
        return 1


def cmd_check(args) -> int:
    run_dir = Path(args.run_dir)
    note = find_note(run_dir, args.note)
    steps: list[dict] = []
    try:
        entry, _ = run_step(
            steps, "lint_note",
            ["lint_note.py", "--note", str(note), "--workdir", str(run_dir),
             "--json-out", str(run_dir / "lint-note.json")],
            allow_fail=True)
    except PipelineError as exc:  # defensive
        entry = exc.entry
    lint = read_json(run_dir / "lint-note.json", {})
    (run_dir / "pipeline-check.json").write_text(
        json.dumps({"steps": steps, "lint": lint}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    if entry["exit_code"] != 0 or not lint.get("ok"):
        print(json.dumps({"ok": False, "ready": False, "gate": "BLOCK UPLOAD",
                          "note": str(note), "lint": lint,
                          "hint": "fix every error in lint-note.json, then re-run check"},
                         ensure_ascii=False, indent=2))
        return 1

    manifest = {
        "ready": True,
        "note_path": str(note),
        "file_name": note.name,
        "file_size": note.stat().st_size,
        "content_type": "text/markdown",
        "file_ext": "md",
        "apply_upload_mode": 1,
        "knowledge_base_id": KB_ID,
        "duplicate_name_strategy": "DUPLICATE_NAME_STRATEGY_REPLACE",
        "folder_id": None,
        "lint": {"gate": lint.get("gate"), "error_count": lint.get("error_count"),
                 "warning_count": lint.get("warning_count")},
        "next": ["1. MCP create_media with the fields above",
                 "2. python upload_presigned.py --file <note> --url <presigned_url> "
                 "--headers-json <auth_headers> --expected-size <file_size>",
                 "3. MCP add_knowledge(knowledge_base_id, media_id, REPLACE)",
                 "4. MCP get_knowledge_list to verify",
                 f'5. python run_pipeline.py finalize --run-dir "{run_dir}" '
                 "--media-id <media_id>"],
    }
    (run_dir / "upload-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, **manifest}, ensure_ascii=False, indent=2))
    return 0


def first_of(obj: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return default


def verify_receipt(receipt: dict, note_name: str, note_bytes: int,
                   note_sha: str = "") -> dict:
    """Cross-check an ima upload receipt against the local note.

    Stage 4 cannot be scripted: ima is a *remote OAuth MCP server*
    (``https://ima.qq.com/mcp``), so the access token belongs to the host
    process, not to this script.  Instead of pretending to drive MCP, `finalize`
    ingests what the assistant actually observed and refuses to record
    ``completed`` unless the numbers agree with the file on disk.  A wrong
    ``note_bytes`` is the most common failure (uploading a stale draft), and it
    is invisible to a human reading a chat log.

    ``note_sha`` is stamped into the result so a later reader can tell *which*
    version of the note was uploaded.  Size alone cannot: v1.10 recorded
    ``verified_file_size=19328`` for a note that had since been rewritten to
    19102 bytes, and a same-length edit would have looked perfectly valid.

    The receipt is either the raw ``get_knowledge_list`` payload (a list) or an
    object wrapping it under ``knowledge_list`` / ``items`` / ``data``.
    """
    entries: list = []
    if isinstance(receipt, list):
        entries = receipt
    elif isinstance(receipt, dict):
        for key in ("knowledge_list", "items", "data", "list", "knowledge"):
            value = receipt.get(key)
            if isinstance(value, list):
                entries = value
                break

    media_id = first_of(receipt, ("media_id", "mediaId", "file_id", "fileId"), "") \
        if isinstance(receipt, dict) else ""
    put_status = first_of(receipt, ("put_status", "putStatus", "http_status"), None) \
        if isinstance(receipt, dict) else None

    target = note_name
    stem = Path(note_name).stem
    hit = None
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = str(first_of(item, ("name", "file_name", "fileName", "title",
                                  "knowledge_name"), ""))
        if name and (name == target or Path(name).stem == stem):
            hit = item
            break

    problems: list[str] = []
    if not entries:
        problems.append("receipt contains no knowledge list entries")
    if hit is None:
        problems.append(f"{note_name} not found in the knowledge list "
                        f"({len(entries)} entries)")
    size = None
    progress = None
    if hit is not None:
        size = first_of(hit, ("size", "file_size", "fileSize", "bytes", "total_size"))
        progress = first_of(hit, ("parse_progress", "parseProgress", "progress"))
        if isinstance(size, str) and size.isdigit():
            size = int(size)
        if isinstance(size, int) and size != note_bytes:
            problems.append(f"size mismatch: ima reports {size}, local file is "
                            f"{note_bytes} (uploaded a stale draft?)")
        if isinstance(progress, int) and progress < 100:
            problems.append(f"ima parse_progress={progress} (still indexing)")
    if put_status is not None:
        try:
            if int(put_status) not in (200, 201, 204):
                problems.append(f"presigned PUT returned {put_status}")
        except (TypeError, ValueError):
            pass

    return {
        "ok": not problems,
        "problems": problems,
        "matched": hit is not None,
        "entry_count": len(entries),
        "entry_name": str(first_of(hit, ("name", "file_name", "fileName", "title",
                                         "knowledge_name"), "")) if hit else "",
        "entry_size": size,
        "parse_progress": progress,
        "media_id": media_id,
        "put_status": put_status,
        # The fingerprint of the exact bytes that were uploaded.  Later readers
        # compare this against the file on disk to spot post-upload edits.
        "note_sha256": note_sha,
    }


def cmd_finalize(args) -> int:
    run_dir = Path(args.run_dir)
    status_path = run_dir / "status.json"
    status = read_json(status_path, {})
    gate = read_json(run_dir / "source" / "transcription-gate.json", {})
    lint = read_json(run_dir / "lint-note.json", {})
    manifest = read_json(run_dir / "upload-manifest.json", {})
    note_name = manifest.get("file_name", status.get("note_file", ""))
    note_path = run_dir / note_name if note_name else None
    note_bytes = note_path.stat().st_size if note_path and note_path.is_file() else 0
    # v1.10 relied on whoever ran finalize to hand-write `note.sha256` into
    # status.json.  That is a metadata field maintained by a human, and so it
    # was absent on every run except the one that happened to write it.  Compute
    # it here, from the file, every time.
    note_sha = sha256_file(note_path) if note_bytes else ""

    verification = None
    if args.receipt:
        receipt = read_json(Path(args.receipt), None)
        if receipt is None:
            print(json.dumps({"ok": False, "error": f"receipt not readable: {args.receipt}"},
                             ensure_ascii=False, indent=2))
            return 1
        verification = verify_receipt(receipt, note_name, note_bytes, note_sha)
        (run_dir / "ima-receipt.json").write_text(
            json.dumps({"receipt": receipt, "verification": verification},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not verification["ok"]:
            print(json.dumps({
                "ok": False, "stage": "finalize", "gate": "RECEIPT MISMATCH",
                "note": note_name, "note_bytes": note_bytes,
                "verification": verification,
                "hint": "status.json NOT marked completed; fix the upload, then re-run "
                        "finalize with a fresh get_knowledge_list receipt",
            }, ensure_ascii=False, indent=2))
            return 1
        if not args.media_id:
            args.media_id = verification.get("media_id") or ""

    ima = {"knowledge_base_id": KB_ID, "media_id": args.media_id,
           "put_status": args.put_status,
           "knowledge_total_size": args.knowledge_total_size,
           "duplicate_name_strategy": "DUPLICATE_NAME_STRATEGY_REPLACE",
           "verified": args.verified or bool(verification and verification["ok"]),
           "verified_file_size": note_bytes,
           "verified_note_sha256": note_sha,
           "verified_at": datetime.now().isoformat(timespec="seconds"),
           "receipt": verification}
    # Merge instead of replace: `note` also carries core_points / chapter_count
    # written by the earlier stage, and a blind dict swap would drop them.
    note_meta = dict(status.get("note") or {})
    note_meta.update({
        "path": str(note_path) if note_path else "",
        "file_name": note_name,
        "bytes": note_bytes,
        "sha256": note_sha,
        "hashed_at": datetime.now().isoformat(timespec="seconds"),
    })
    status.update({
        "status": "completed", "next_step": "none",
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "note": note_meta,
        "note_file": note_name, "note_bytes": note_bytes,
        "deliverable": gate.get("deliverable", "unknown"),
        "lint": {"gate": lint.get("gate"), "error_count": lint.get("error_count")},
        "ima": ima,
    })
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")

    dl = status.get("download", {})
    chapters = status.get("chapters", {})
    lines = [
        f"# {status.get('video_id', '')} · 运行状态", "",
        "**状态：completed**", "",
        "| 阶段 | 结果 |", "| --- | --- |",
        f"| 0 输入归一化 | `{status.get('input_resolution', {}).get('input_type', '')}`，"
        f"{status.get('input_resolution', {}).get('hops', 0)} 跳展开 |",
        f"| 1C 下载 | `{dl.get('pipeline_status', '')}` / "
        f"`failure_class={dl.get('failure_class', '')}` / "
        f"{dl.get('media_validation', {}).get('size', 0):,} bytes |",
        f"| 1E 门禁 | `{gate.get('deliverable', '')}` / state=`{gate.get('state', '')}` |",
        f"| 2 章节 | 共 {chapters.get('count', 0)} 条，来源 `{chapters.get('source_path', '')}` |",
        f"| 3 蒸馏 | `{note_name}`（{note_bytes:,} bytes） |",
        f"| lint | `{lint.get('gate', '')}`，{lint.get('error_count', 0)} error / "
        f"{lint.get('warning_count', 0)} warning |",
        f"| 4 入库 | PUT {args.put_status} → REPLACE → 知识库共 "
        f"{args.knowledge_total_size} 条 |",
        "", "## 说明", "",
        "- `status.json` 是唯一机器可读状态；`pipeline-steps.json` 记录每条命令与退出码。",
        "- 笔记通过 `lint_note.py` 后才允许上传，产物与中间文件均在 D 盘。",
        "- 交付类型按证据来源区分（逐字稿型 / 章节型），两者都是完整正式笔记。",
    ]
    (run_dir / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "stage": "finalized", "status": "completed",
                      "note": note_name, "bytes": note_bytes, "ima": ima,
                      "status_file": str(status_path)},
                     ensure_ascii=False, indent=2))
    return 0


MEDIA_SUFFIXES = frozenset({".mp4", ".mp3", ".m4a", ".aac", ".wav", ".flv", ".webm"})


def dir_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def scan_runs(video_id_filter: str) -> list[dict]:
    """Classify every run<N> dir under DISTILL_ROOT."""
    runs: list[dict] = []
    if not DISTILL_ROOT.is_dir():
        return runs
    for video_dir in sorted(DISTILL_ROOT.iterdir()):
        if not video_dir.is_dir() or not VIDEO_ID_RE.fullmatch(video_dir.name):
            continue
        if video_id_filter and video_dir.name != video_id_filter:
            continue
        for run_dir in sorted(video_dir.iterdir()):
            # `run3b` style suffixes exist (manual side-branch attempts); ignoring
            # them made clean silently leave junk behind, so fold them into the
            # same sequence with the suffix as tie-breaker.
            match = re.fullmatch(r"run(\d+)([a-z]*)", run_dir.name)
            if not run_dir.is_dir() or not match:
                continue
            status = read_json(run_dir / "status.json", {})
            state = status.get("status", "") or "unknown"
            notes = [p for p in run_dir.glob("*.md")
                     if p.name != "STATUS.md" and "素材卡" not in p.name]
            runs.append({
                "video_id": video_dir.name,
                "run": run_dir.name,
                "seq": int(match.group(1)),
                "suffix": match.group(2),
                "path": str(run_dir),
                "status": state,
                "completed": state == "completed",
                "has_note": bool(notes),
                "mtime": run_dir.stat().st_mtime,
                "bytes": dir_bytes(run_dir),
            })
    return runs


def cmd_clean(args) -> int:
    runs = scan_runs(args.video_id)
    by_video: dict[str, list[dict]] = {}
    for row in runs:
        by_video.setdefault(row["video_id"], []).append(row)

    keep_last = max(1, args.keep_last)
    actions: list[dict] = []
    for video_id, group in by_video.items():
        # Newest first: sequence number is the tie-breaker when mtimes collide
        # (same-second runs), because run<N> numbering is monotonic by design.
        ordered = sorted(group,
                         key=lambda r: (r["seq"], r.get("suffix", ""), r["mtime"]),
                         reverse=True)
        for index, row in enumerate(ordered):
            if index < keep_last:
                row["action"] = "keep"
                actions.append({**row, "reason": f"within --keep-last {keep_last}"})
                continue
            if row["completed"]:
                # Deliberate deviation from nextflow: published runs keep their
                # audit trail, only the bulky media goes.
                media = [p for p in (Path(row["path"]) / "api").glob("*")
                         if p.suffix.lower() in MEDIA_SUFFIXES] \
                    if (Path(row["path"]) / "api").is_dir() else []
                freed = sum(p.stat().st_size for p in media if p.is_file())
                actions.append({**row, "action": "prune-media", "reason":
                                "completed run: keep note + status audit trail",
                                "files": [str(p) for p in media], "freed": freed})
            else:
                actions.append({**row, "action": "delete", "reason":
                                f"abandoned run (status={row['status']}), no audit value",
                                "freed": row["bytes"]})

    target_total = sum(a.get("freed", 0) for a in actions)
    payload = {
        "ok": True, "dry_run": not args.force,
        "distill_root": str(DISTILL_ROOT), "keep_last": keep_last,
        "run_count": len(runs),
        "actions": actions,
        "reclaimable_bytes": target_total,
        "reclaimable_human": f"{target_total / (1 << 20):.1f} MiB",
        "hint": "dry run only; add -f to apply" if not args.force else "applied",
    }

    if args.force:
        applied = []
        for act in actions:
            if act["action"] == "delete":
                try:
                    shutil.rmtree(act["path"])
                    applied.append({"path": act["path"], "result": "deleted"})
                except OSError as exc:
                    applied.append({"path": act["path"], "result": f"failed: {exc}"})
            elif act["action"] == "prune-media":
                removed = 0
                for name in act.get("files", []):
                    try:
                        Path(name).unlink()
                        removed += 1
                    except OSError:
                        pass
                applied.append({"path": act["path"], "result":
                                f"pruned {removed}/{len(act.get('files', []))} media files"})
        payload["applied"] = applied

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="抖音视频精选 pipeline orchestrator")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("raw", help="整段分享口令 / 短链 / URL / 纯 id")
    p.add_argument("--run-dir", default="", help=f"default: {DISTILL_ROOT}/<id>/run<N>")
    p.add_argument("--no-download", action="store_true",
                   help="reuse existing api/detail.json (offline test)")
    p.add_argument("--use-local-asr", action="store_true",
                   help="transcribe with local sherpa-onnx instead of cloud ASR")
    p.add_argument("--asr-root", default=str(ASR_ROOT))
    p.add_argument("--transcription-state", default="",
                   choices=["", "done", "declined", "failed", "granted", "skipped"])
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_prepare)

    c = sub.add_parser("check")
    c.add_argument("--run-dir", required=True)
    c.add_argument("--note", default="")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("finalize")
    f.add_argument("--run-dir", required=True)
    f.add_argument("--media-id", default="")
    f.add_argument("--put-status", type=int, default=200)
    f.add_argument("--knowledge-total-size", type=int, default=0)
    f.add_argument("--verified", action="store_true")
    f.add_argument("--receipt", default="",
                   help="JSON of the real get_knowledge_list result; when given, "
                        "status=completed is written only if it matches the local note")
    f.set_defaults(func=cmd_finalize)

    cl = sub.add_parser("clean", help="prune old run dirs; dry-run unless -f")
    cl.add_argument("--video-id", default="", help="limit to one video id")
    cl.add_argument("--keep-last", type=int, default=2,
                    help="keep the N newest runs per video (default 2)")
    cl.add_argument("-n", "--dry-run", action="store_true", default=True,
                    help="report only (default)")
    cl.add_argument("-f", "--force", dest="force", action="store_true",
                    help="actually delete / prune")
    cl.set_defaults(func=cmd_clean)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
