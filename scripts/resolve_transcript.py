#!/usr/bin/env python3
"""Stage 1A fallback: reuse a previously verified transcript, safely.

Problem this fixes
------------------
The Skill used to have only "call the ASR tool" for stage 1B.  If that call is
declined, fails, or is too expensive to repeat, the pipeline stalls with no
documented path forward, even when an identical video and a real transcript
already exist on disk.

This script makes reuse an explicit, auditable stage:

  1. Prefer the workdir's own source/transcript.md.
  2. Otherwise look in --source (defaults to the parent run directory).
  3. Reject source cards / anything without ASR timestamp markers.
  4. Require a SHA256 match between the current mp4 and the mother mp4 (or an
     explicit hash recorded in the transcript header).  No match => refuse.
  5. Write the reused transcript with a provenance header and merge state into
     the single authoritative status.json.

Usage:
    python resolve_transcript.py --workdir "D:/douyin-distill/<id>/run2" \
        [--source "D:/douyin-distill/<id>"] [--mp4 "<workdir>/api/<id>.mp4"]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dy_http import merge_status  # noqa: E402

TIMESTAMP_RE = re.compile(r"\[\d+:\d+\.\d+,\d+:\d+\.\d+")
SOURCE_CARD_MARKERS = ("素材卡", "蒸馏未完成", "SOURCE-CARD")
SHA_RE = re.compile(r"\b[0-9a-f]{64}\b")


LOCAL_ASR_HEADER = "本地离线转写（sherpa-onnx"
LINE_TS_RE = re.compile(r"^\[\d+:\d+\.\d+,\d+:\d+\.\d+\]")


def looks_like_transcript(text: str) -> tuple[bool, str]:
    head = text[:800]
    if any(marker in head for marker in SOURCE_CARD_MARKERS):
        return False, "candidate is a source card, not a transcript"
    if not TIMESTAMP_RE.search(text):
        return False, "no ASR timestamp markers found"
    # A locally generated transcript has a signed header (engine + model +
    # source audio).  Short videos legitimately produce short transcripts, so
    # require >=3 timestamped lines and 200 chars instead of the 1000-char
    # cloud-ASR threshold.  Source cards still fail on the marker check.
    if LOCAL_ASR_HEADER in head:
        rows = sum(1 for line in text.splitlines() if LINE_TS_RE.match(line))
        if rows < 3:
            return False, f"local ASR transcript has only {rows} timestamped lines"
        if len(text) < 200:
            return False, "local ASR transcript too short to be a real verbatim source"
        return True, "ok"
    if len(text) < 1000:
        return False, "transcript too short to be a real verbatim source"
    return True, "ok"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_video_id(workdir: Path) -> str:
    for candidate in (workdir.name, workdir.parent.name):
        if re.fullmatch(r"\d{15,25}", candidate):
            return candidate
    status = workdir / "status.json"
    if status.is_file():
        try:
            return str(json.loads(status.read_text(encoding="utf-8")).get("video_id", ""))
        except (ValueError, OSError):
            pass
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--source", default="",
                    help="previous run directory holding source/transcript.md")
    ap.add_argument("--mp4", default="", help="current video for SHA256 verification")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "source").mkdir(exist_ok=True)
    status_file = workdir / "status.json"
    target = workdir / "source" / "transcript.md"
    video_id = find_video_id(workdir)

    out: dict = {"video_id": video_id, "workdir": str(workdir)}

    if target.is_file():
        ok, why = looks_like_transcript(target.read_text(encoding="utf-8"))
        if ok:
            out.update({"ok": True, "transcript_source": "workdir",
                        "reused": False, "note": "A verified transcript already exists."})
            merge_status(status_file, status="transcript_ready", transcript=out)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

    source_dir = Path(args.source) if args.source else workdir.parent
    candidate = source_dir / "source" / "transcript.md"
    if not candidate.is_file():
        out.update({"ok": False, "error": "no_reusable_transcript",
                    "looked_in": str(candidate),
                    "hint": "Ask the user for subtitles, or provide --source."})
        merge_status(status_file, status="transcript_missing", transcript=out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 3

    text = candidate.read_text(encoding="utf-8")
    ok, why = looks_like_transcript(text)
    if not ok:
        out.update({"ok": False, "error": "candidate_rejected", "reason": why,
                    "candidate": str(candidate)})
        merge_status(status_file, status="transcript_missing", transcript=out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 4

    current_mp4 = Path(args.mp4) if args.mp4 else workdir / "api" / f"{video_id}.mp4"
    if not current_mp4.is_file():
        out.update({"ok": False, "error": "current_mp4_missing",
                    "expected": str(current_mp4),
                    "hint": "Run douyin_detail.py --download first; SHA256 "
                            "verification is mandatory for reuse."})
        merge_status(status_file, status="transcript_missing", transcript=out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 5

    current_hash = sha256_file(current_mp4)
    mother_mp4 = source_dir / "api" / f"{video_id}.mp4"
    if not mother_mp4.is_file():
        mother_mp4 = source_dir / f"{video_id}.mp4"
    recorded = set(SHA_RE.findall(text[:2000]))

    verified_by = ""
    if mother_mp4.is_file() and sha256_file(mother_mp4) == current_hash:
        verified_by = f"mother mp4 byte-identical ({mother_mp4})"
    elif current_hash in recorded:
        verified_by = "SHA256 recorded in the transcript header"
    else:
        out.update({"ok": False, "error": "sha256_mismatch",
                    "current_sha256": current_hash,
                    "mother_mp4": str(mother_mp4),
                    "hint": "Refusing to reuse: the video bytes differ from the "
                            "transcript's source. Re-transcribe instead."})
        merge_status(status_file, status="transcript_missing", transcript=out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 6

    # A reused transcript may itself already carry a provenance header (run2
    # -> run5 -> run6 ...).  Strip leading header lines so we never stack two
    # or three "复用说明" blocks on top of each other.
    lines = text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if stripped.startswith(">") and any(marker in stripped for marker in (
                "复用说明", "未重新执行音频转写", "母版校验", "当前视频 SHA256")):
            idx += 1
            continue
        break
    body = "".join(lines[idx:])

    header = (
        f"> **复用说明（stage 1A）**：本稿复用自 `{candidate}`，\n"
        f"> 未重新执行音频转写。母版校验：{verified_by}；\n"
        f"> 当前视频 SHA256 = `{current_hash}`。文字来源仍为本条视频，未凭记忆补写。\n\n"
    )
    target.write_text(header + body, encoding="utf-8")
    out.update({"ok": True, "transcript_source": str(candidate),
                "reused": True, "verified_by": verified_by,
                "sha256": current_hash, "written_to": str(target),
                "bytes": target.stat().st_size})
    merge_status(status_file, status="transcript_ready", transcript=out,
                 next_step="Stage 2: build source/meta.md from api/detail.json, "
                           "then stage 3 distill.")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
