#!/usr/bin/env python3
"""Stage 1/1B gate: decide what may be delivered when transcription is blocked.

Problem this fixes (reproduced 2026-11-08)
------------------------------------------
The ASR tool call was declined twice.  The pipeline stopped, but status.json
still said ``chapters_ready`` and ``next_step = Stage 3: include the chapter
section in the note`` - as if a full note were ready to write.  A resume from
that checkpoint would have produced a formal note with no verbatim source,
silently violating the red line.

Design borrowed from GitHub
---------------------------
Evil0ctal/Douyin_TikTok_Download_API src/dtk/services/transcriptions.py:
  * TERMINAL_STATES = ("done", "failed", "cancelled") - failure is a real
    terminal state, never a silent continuation.
  * NORMALIZATION_FAILURE_POLICIES = {"fallback", "fail"} - the failure policy
    is explicit and configurable, not an accident of control flow.
  * as_dict(..., reused=...) - reuse is labelled, never disguised.

This gate maps those ideas onto the Skill:
  * ``declined`` / ``failed`` are terminal; ``no_silent_retry`` is recorded.
  * ``policy=fail`` blocks all output; ``policy=fallback`` picks the highest
    evidence level actually available.
  * deliverable is one of full_note / limited_note / source_card / blocked.
  * status.json's top-level status and next_step are OVERWRITTEN so a resume
    can never read a stale "go write the note" instruction.

Usage:
    python transcription_gate.py --workdir "<workdir>" --state declined \
        --reason "AudioTranscribe rejected twice by tool approval"
    python transcription_gate.py --workdir "<workdir>" --state done
    python transcription_gate.py --workdir "<workdir>" --state declined --policy fail
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dy_http import merge_status  # noqa: E402
from resolve_transcript import looks_like_transcript  # noqa: E402

TERMINAL_STATES = frozenset({"done", "failed", "cancelled"})
FAILURE_POLICIES = frozenset({"fallback", "fail"})
VALID_STATES = frozenset({"done", "declined", "failed", "granted", "skipped"})

PY_BACKENDS = {
    "faster_whisper": "faster_whisper",
    "funasr": "funasr",
    "sherpa_onnx": "sherpa_onnx",
    "openai_whisper": "whisper",
}
EXE_BACKENDS = ("whisper-cli", "whisper", "main", "ffmpeg")

NEXT_STEP = {
    "full_note": "Stage 3: write the formal note with 【原文】 evidence and timestamps.",
    "limited_note": ("Stage 3: write a normal 精选笔记 from official chapters + author "
                     "copy. State evidence sources honestly in 来源与证据; NEVER use "
                     "self-deprecating banners (未完成/缺口/有限笔记/不覆盖全部内容) "
                     "and never emit 【原文】 claims, reserved for verbatim speech."),
    "source_card": ("If a local ASR backend is ready and api/<id>.mp4 or .mp3 exists, "
                    "FIRST run scripts/local_asr.py --workdir <R>, then rerun this gate "
                    "with --state done; a real transcript upgrades this to full_note. "
                    "Only when no audio and no backend exist: stop distillation, emit an "
                    "explicitly-marked 素材卡（未完成）, ask for subtitles or a local file."),
    "blocked": ("Stop. Transcription is a terminal failure and policy=fail. "
                "Wait for an explicit user instruction; do NOT retry the tool."),
    "transcription_pending": ("Call the transcription tool (only with explicit user "
                              "authorization), then rerun this gate with --state done."),
}
STATUS = {
    "full_note": "transcript_ready",
    "limited_note": "limited_note_allowed",
    "source_card": "source_card_required",
    "blocked": "transcription_blocked",
    "transcription_pending": "transcription_pending",
}


def probe_backends() -> list[dict]:
    out = []
    for label, module in PY_BACKENDS.items():
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            available = False
        out.append({"name": label, "kind": "python", "available": available})
    exe_found = {name: shutil.which(name) for name in EXE_BACKENDS}
    for name, path in exe_found.items():
        out.append({"name": name, "kind": "exe", "available": bool(path),
                    "path": path or ""})
    # v1.7: the offline backend is installed on the D drive outside this
    # interpreter's sys.path, so find_spec() cannot see it.  Ask the script
    # that owns the installation instead.
    script = Path(__file__).resolve().parent / "local_asr.py"
    entry = {"name": "sherpa_onnx_sensevoice_local", "kind": "local",
             "available": False, "path": str(script)}
    if script.is_file():
        try:
            proc = subprocess.run([sys.executable, str(script), "--probe"],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=30)
            report = json.loads(proc.stdout)
            entry.update({
                "available": bool(report.get("ok")),
                "engine": report.get("engine", ""),
                "engine_version": report.get("engine_version", ""),
                "segmentation": report.get("segmentation", ""),
                "model": report.get("paths", {}).get("model", ""),
            })
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            entry["error"] = str(exc)[:150]
    out.append(entry)
    return out


def read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--state", required=True, choices=sorted(VALID_STATES))
    ap.add_argument("--reason", default="")
    ap.add_argument("--policy", default="fallback", choices=sorted(FAILURE_POLICIES))
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "source"
    source.mkdir(exist_ok=True)
    status_file = workdir / "status.json"

    transcript_file = source / "transcript.md"
    has_transcript = False
    transcript_reason = "missing"
    if transcript_file.is_file():
        ok, why = looks_like_transcript(transcript_file.read_text(encoding="utf-8"))
        has_transcript = ok
        transcript_reason = why

    subtitle_probe = read_json(source / "subtitle-probe.json", {})
    has_subtitles = bool(subtitle_probe.get("ok"))
    chapters = read_json(source / "chapters.json", {})
    chapter_count = int(chapters.get("count", 0) or 0)

    backends = probe_backends()
    offline_ready = any(b["available"] and b["name"] != "ffmpeg" for b in backends)

    checks = {
        "transcript": {"path": str(transcript_file), "ok": has_transcript,
                       "reason": transcript_reason},
        "subtitles": {"path": str(source / "subtitle-probe.json"), "ok": has_subtitles,
                      "source_path": subtitle_probe.get("source_path", "")},
        "chapters": {"path": str(source / "chapters.json"), "ok": chapter_count > 0,
                     "count": chapter_count},
        "local_asr": {"ok": offline_ready,
                      "note": ("local sherpa-onnx SenseVoice + silero VAD ready on D drive"
                               if offline_ready else "no local ASR backend installed")},
    }

    blocked = (args.state in ("declined", "failed", "granted", "skipped")
               and args.policy == "fail" and not (has_transcript or has_subtitles))
    if blocked:
        deliverable = "blocked"
    elif has_transcript or has_subtitles:
        deliverable = "full_note"
    elif args.state == "granted":
        deliverable = "transcription_pending"
    elif chapter_count > 0:
        deliverable = "limited_note"
    else:
        deliverable = "source_card"

    terminal = args.state in TERMINAL_STATES or args.state == "declined"
    gate = {
        "ok": deliverable not in ("blocked", "source_card"),
        "state": args.state,
        "reason": args.reason,
        "policy": args.policy,
        "terminal": terminal,
        "no_silent_retry": args.state in ("declined", "failed"),
        "deliverable": deliverable,
        "evidence_level": {
            "full_note": "verbatim transcript / official subtitles",
            "limited_note": "official chapters + total abstract + author copy; a "
                           "complete deliverable, NOT verbatim speech",
            "source_card": "metadata only; distillation forbidden",
            "blocked": "none",
            "transcription_pending": "awaiting ASR result",
        }[deliverable],
        "checks": checks,
        "local_backends": backends,
        "offline_option": (
            "Local offline ASR ready: python scripts/local_asr.py --workdir <R> "
            "(sherpa-onnx SenseVoice int8 + silero VAD, installed on D drive); "
            "no cloud tool required. See references/douyin-download.md §12"
            if offline_ready else
            "No local ASR installed; install per references/douyin-download.md §12 "
            "or paste a transcript (stage 1A)"),
        "next_step": NEXT_STEP[deliverable],
    }

    (source / "transcription-gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")

    merge_status(
        status_file,
        status=STATUS[deliverable],
        transcription=gate,
        next_step=NEXT_STEP[deliverable],
    )
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    return 0 if deliverable != "blocked" else 4


if __name__ == "__main__":
    raise SystemExit(main())
