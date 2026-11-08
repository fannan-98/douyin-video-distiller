#!/usr/bin/env python3
"""Normalize a Douyin video input and, when possible, download the media file.

Outputs a JSON status object on stdout and writes status.json into the workdir.
This script never fabricates transcript text; it only prepares media for ASR.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dy_config import DISTILL_ROOT

VIDEO_ID_RE = re.compile(r"\d{15,25}")
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".flv", ".mp3", ".m4a",
              ".wav", ".aac", ".ogg", ".flac", ".3gp", ".wma"}
MAX_ASR_BYTES = 1024 * 1024 * 1024  # 1 GB
PROXY_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "ALL_PROXY", "all_proxy")


def extract_video_id(raw: str):
    """v1.5: delegate to resolve_input (short-link expansion + host allowlist).

    The old one-line regex returned None for a real Douyin share paste because
    short codes contain no numeric id.  It also risked matching an unrelated
    15+ digit number in share text.  resolve_input handles explicit ids,
    /video/<id>, modal_id, bare URL and v.douyin.com short chains, and returns
    an auditable report so status.json records HOW the id was obtained.
    """
    try:
        from resolve_input import resolve, ResolveError  # noqa: PLC0415
    except ImportError:
        match = VIDEO_ID_RE.search(raw or "")
        return (match.group(0) if match else None), {"ok": bool(match),
                                                     "input_type": "legacy_regex"}
    try:
        report = resolve(raw)
        return report["video_id"], report
    except Exception as exc:  # noqa: BLE001 - ResolveError / network errors alike
        failure = getattr(exc, "failure_class", "network_error")
        return None, {"ok": False, "failure_class": failure, "error": str(exc)}


def clean_env():
    env = dict(os.environ)
    for key in PROXY_KEYS:
        env.pop(key, None)
    # Force direct connection; the local registry proxy can break TLS handshakes.
    env["no_proxy"] = "*"
    env["NO_PROXY"] = "*"
    return env


def describe_local_file(path: Path):
    if not path.is_file():
        return {"ok": False, "reason": "file_not_found", "path": str(path)}
    size = path.stat().st_size
    ext = path.suffix.lower()
    return {
        "ok": size > 0 and ext in MEDIA_EXTS and size <= MAX_ASR_BYTES,
        "reason": "ok" if size > 0 and ext in MEDIA_EXTS and size <= MAX_ASR_BYTES
                  else ("unsupported_ext" if ext not in MEDIA_EXTS else
                        "too_large" if size > MAX_ASR_BYTES else "empty_file"),
        "path": str(path),
        "size": size,
        "ext": ext,
    }


def run(cmd, env, timeout=180):
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
        return {"returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-4000:],
                "stderr": (proc.stderr or "")[-4000:]}
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "timeout"}
    except Exception as exc:  # noqa: BLE001 - report any launch failure
        return {"returncode": -1, "stdout": "", "stderr": str(exc)}


def main():
    parser = argparse.ArgumentParser(description="Douyin media fetch helper")
    parser.add_argument("input", help="video URL, share link, modal_id or raw video id")
    parser.add_argument("--workdir", default="", help="output directory (D drive preferred)")
    parser.add_argument("--local-file", default="", help="already saved local media file")
    parser.add_argument("--cookies", default="", help="Netscape cookie file for yt-dlp")
    parser.add_argument("--cookies-from-browser", default="",
                        help='e.g. "edge:$DOUYIN_DISTILL_ROOT/edge-profile"')
    parser.add_argument("--format", default="best[height<=720]/best",
                        help="yt-dlp format selector")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--normalize-only", action="store_true",
                        help="stage 0 only: parse the input and set up workdir, do NOT run yt-dlp")
    parser.add_argument("--json-out", default="", help="also write status JSON here")
    args = parser.parse_args()

    video_id, resolution = extract_video_id(args.input)
    result = {
        "tool": "douyin_fetch.py",
        "time": datetime.now().isoformat(timespec="seconds"),
        "input": args.input,
        "video_id": video_id,
        "canonical_url": ("https://www.douyin.com/video/" + video_id) if video_id else None,
        "input_resolution": resolution,
        "status": "unknown",
        "next_step": None,
    }

    if not video_id:
        result["status"] = "invalid_input"
        reason = resolution.get("failure_class", "unknown")
        result["next_step"] = (
            f"Input resolution failed ({reason}): {resolution.get('error', '')}. "
            "Ask the user to paste the FULL share text (including the "
            "https://v.douyin.com/... link) or a /video/<id> link."
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    workdir = Path(args.workdir) if args.workdir else DISTILL_ROOT / video_id
    workdir.mkdir(parents=True, exist_ok=True)
    result["workdir"] = str(workdir)

    if args.normalize_only:
        # v1.2 stage 0. The signed downloader (scripts/douyin_detail.py) owns
        # stage 1C now; running yt-dlp here would only produce a misleading
        # "do not distill or upload" status file.
        (workdir / "source").mkdir(exist_ok=True)
        result["status"] = "ready_for_signed_download"
        result["next_step"] = (
            "Run scripts/douyin_detail.py " + video_id +
            " --download --out <workdir>/api; then extract_chapters.py, "
            "probe_subtitles.py, and transcription_gate.py before any note is written."
        )
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        (workdir / "status.json").write_text(payload, encoding="utf-8")
        if args.json_out:
            Path(args.json_out).write_text(payload, encoding="utf-8")
        print(payload)
        return 0

    if args.local_file:
        info = describe_local_file(Path(args.local_file))
        result["local_file"] = info
        if info["ok"]:
            result["status"] = "ready_for_transcribe"
            result["next_step"] = ("Call AudioTranscribe with filePath="
                                   + info["path"] + ", then save output to source/transcript.md")
        else:
            result["status"] = "invalid_local_file"
            result["next_step"] = "Ask the user for a supported media file (<=1GB)."
    else:
        ytdlp = shutil.which("yt-dlp")
        if not ytdlp:
            result["status"] = "yt_dlp_missing"
            result["next_step"] = ("Install yt-dlp, or ask the user for a subtitle / "
                                   "a locally saved video file.")
        else:
            out_tpl = str(workdir / "%(id)s.%(ext)s")
            cmd = [ytdlp, "--no-warnings", "--no-playlist",
                   "-f", args.format, "-o", out_tpl]
            if args.cookies:
                cmd += ["--cookies", args.cookies]
            if args.cookies_from_browser:
                cmd += ["--cookies-from-browser", args.cookies_from_browser]
            cmd.append(result["canonical_url"])
            proc = run(cmd, clean_env(), args.timeout)
            result["command"] = " ".join(cmd)
            result["process"] = proc

            produced = [p for p in workdir.iterdir()
                        if p.is_file() and p.suffix.lower() in MEDIA_EXTS
                        and p.stat().st_size > 0]
            if produced:
                media = max(produced, key=lambda p: p.stat().st_mtime)
                result["media"] = describe_local_file(media)
                result["status"] = "ready_for_transcribe"
                result["next_step"] = ("Call AudioTranscribe with filePath="
                                       + str(media) + ", then save output to source/transcript.md")
            else:
                err = proc.get("stderr", "")
                if "Fresh cookies" in err:
                    reason = ("Douyin detail API needs a real s_v_web_id cookie and a_bogus "
                              "signature. Do NOT retry with fake cookies or ttwid.")
                elif "Could not copy Chrome cookie database" in err:
                    reason = ("Edge holds an exclusive lock on the Cookie DB. "
                              "Fully quit Edge and retry, or use a copied profile snapshot.")
                else:
                    reason = "Download failed; see references/douyin-download.md."
                result["status"] = "download_failed"
                result["reason"] = reason
                result["next_step"] = (
                    "yt-dlp is the v1.1 fallback. First try the v1.2 signed downloader: "
                    "scripts/douyin_detail.py <id> --download. Only if that also fails, "
                    "fall back to assets/note-template.md template B (source card) and stop."
                )
                result["hint"] = ("Other success paths: ask the user for subtitles or a "
                                  "locally saved video file; ASR needs no cookies.")

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    (workdir / "status.json").write_text(payload, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if result["status"] == "ready_for_transcribe" else 1


if __name__ == "__main__":
    sys.exit(main())
