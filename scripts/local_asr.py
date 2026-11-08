#!/usr/bin/env python3
"""Stage 1B offline path: transcribe audio locally with sherpa-onnx SenseVoice.

Why this exists
---------------
The pipeline used to depend on a single cloud ASR tool.  When that tool call was
declined twice (2026-11-08), the whole run had to fall back to chapter-based
notes even though the mp4/mp3 was sitting on disk.  This script removes that
single point of failure.

The backend lives entirely on the D drive so the C drive (4.5 GiB free) is
never touched:

    $WORKBUDDY_ASR_ROOT\\site\\                  sherpa-onnx + numpy (pip --target)
    $WORKBUDDY_ASR_ROOT\\models\\sense-voice\\   model.int8.onnx + tokens.txt

Output is a Skill-compatible transcript: `[m:ss.mmm,m:ss.mmm] text` lines that
`resolve_transcript.looks_like_transcript` accepts, so `transcription_gate.py
--state done` upgrades the deliverable to `full_note` automatically.

Usage
-----
    python local_asr.py --probe
    python local_asr.py --workdir "<R>" --audio "<R>/api/<id>.mp3"
    python local_asr.py --workdir "<R>" --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

from dy_config import ASR_ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dy_http import merge_status  # noqa: E402
from resolve_transcript import looks_like_transcript  # noqa: E402

DEFAULT_ROOT = ASR_ROOT
SAMPLE_RATE = 16000
FRAME = 320                  # 20 ms
MAX_SEGMENT_S = 25.0
MIN_SEGMENT_S = 0.30
SILENCE_S = 0.40
PAD_S = 0.20


def asr_paths(root: Path) -> dict:
    site = root / "site"
    model_dir = root / "models" / "sense-voice"
    return {
        "root": str(root),
        "site": str(site),
        "model": str(model_dir / "model.int8.onnx"),
        "tokens": str(model_dir / "tokens.txt"),
        "vad": str(model_dir / "silero_vad.onnx"),
        "model_size": (model_dir / "model.int8.onnx").stat().st_size
        if (model_dir / "model.int8.onnx").is_file() else 0,
    }


def probe(root: Path) -> dict:
    p = asr_paths(root)
    site = Path(p["site"])
    model = Path(p["model"])
    tokens = Path(p["tokens"])
    ok_module = (site / "sherpa_onnx" / "__init__.py").is_file()
    ok_numpy = (site / "numpy" / "__init__.py").is_file()
    ok_model = model.is_file() and model.stat().st_size > 200_000_000
    ok_tokens = tokens.is_file() and tokens.stat().st_size > 100_000
    vad = Path(p["vad"])
    vad_ok = vad.is_file() and vad.stat().st_size > 100_000
    engine_version = ""
    if ok_module:
        sys.path.insert(0, str(site))
        try:
            import sherpa_onnx  # noqa: PLC0415
            engine_version = getattr(sherpa_onnx, "__version__", "")
        except Exception:  # noqa: BLE001
            ok_module = False
    return {
        "ok": all((ok_module, ok_numpy, ok_model, ok_tokens)),
        "engine": "sherpa-onnx / SenseVoice int8 / CPU",
        "engine_version": engine_version,
        "checks": {"module": ok_module, "numpy": ok_numpy,
                   "model": ok_model, "tokens": ok_tokens, "silero_vad": vad_ok},
        "segmentation": "silero VAD" if vad_ok else "energy silence (fallback)",
        "paths": p,
        "model_size": p["model_size"],
        "ffmpeg": shutil.which("ffmpeg") or "",
    }


def to_wav(audio: Path, wav_path: Path) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")
    import subprocess  # noqa: PLC0415
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(audio), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
           "-c:a", "pcm_s16le", str(wav_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not wav_path.is_file():
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[:300]}")
    return {"cmd": "ffmpeg -vn -ac 1 -ar 16000 -c:a pcm_s16le",
            "wav": str(wav_path), "bytes": wav_path.stat().st_size}


def read_samples(wav_path: Path):
    import numpy as np  # noqa: PLC0415
    with wave.open(str(wav_path), "rb") as handle:
        if handle.getframerate() != SAMPLE_RATE:
            raise RuntimeError(f"unexpected sample rate {handle.getframerate()}")
        if handle.getnchannels() != 1:
            raise RuntimeError("expected mono wav")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def split_segments(samples):
    """Energy-based silence split; avoids cutting mid-sentence where possible."""
    import numpy as np  # noqa: PLC0415
    total = len(samples)
    if total == 0:
        return []
    n_frames = total // FRAME
    frames = samples[:n_frames * FRAME].reshape(n_frames, FRAME)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    noise = float(np.percentile(rms, 20))
    threshold = max(noise * 2.5, 0.006)
    quiet = rms < threshold

    silence_frames = max(1, int(SILENCE_S * SAMPLE_RATE / FRAME))
    pad = int(PAD_S * SAMPLE_RATE)
    max_len = int(MAX_SEGMENT_S * SAMPLE_RATE)
    min_len = int(MIN_SEGMENT_S * SAMPLE_RATE)

    cuts = []
    run = 0
    for i, q in enumerate(quiet):
        run = run + 1 if q else 0
        if run >= silence_frames:
            center = (i - run // 2) * FRAME
            if not cuts or center - cuts[-1] > min_len:
                cuts.append(center)
            run = 0

    segments, start = [], 0
    for cut in cuts + [total]:
        # force a cut when a stretch has no silence for too long
        while cut - start > max_len:
            end = start + max_len
            segments.append((max(0, start - pad), min(total, end + pad)))
            start = end
        if cut - start >= min_len:
            segments.append((max(0, start - pad), min(total, cut + pad)))
        start = cut
    return [(a, b) for a, b in segments if b - a >= min_len]


def vad_segments(samples, vad_model: Path):
    """Silero VAD segmentation. Much cleaner than raw energy splitting: it
    drops music/silence stretches that the ASR would otherwise hallucinate
    into '。' / '嗯' / '我' one-character lines."""
    import numpy as np  # noqa: PLC0415, F401
    import sherpa_onnx  # noqa: PLC0415
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(vad_model)
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.25
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 20.0
    cfg.silero_vad.window_size = 512
    if hasattr(cfg, "sample_rate"):
        cfg.sample_rate = SAMPLE_RATE
    detector = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
    window = 512
    for i in range(0, len(samples), window):
        chunk = samples[i:i + window]
        if len(chunk) < window:
            chunk = np.pad(chunk, (0, window - len(chunk)))
        detector.accept_waveform(chunk)
    detector.flush()
    out = []
    min_len = int(MIN_SEGMENT_S * SAMPLE_RATE)
    while not detector.empty():
        seg = detector.front
        start = int(seg.start)
        n = len(seg.samples)
        if n >= min_len:
            out.append((max(0, start), min(len(samples), start + n)))
        detector.pop()
    return out


def usable_text(text: str) -> bool:
    """Reject punctuation-only and one-character ASR hallucinations."""
    import re  # noqa: PLC0415
    t = text.strip()
    if len(t) < 4:
        return False
    if re.fullmatch(r"[\s\W_]+", t, flags=re.UNICODE):
        return False
    han = len(re.findall(r"[\u4e00-\u9fff]", t))
    if han < 3 and not re.search(r"[A-Za-z0-9]{3,}", t):
        return False
    return True


def fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}:{s:06.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline ASR via sherpa-onnx SenseVoice")
    ap.add_argument("--workdir", default="", help="run directory")
    ap.add_argument("--audio", default="", help="mp3/mp4/wav; auto-detected if omitted")
    ap.add_argument("--asr-root", default=str(DEFAULT_ROOT))
    ap.add_argument("--probe", action="store_true", help="only report backend readiness")
    ap.add_argument("--force", action="store_true", help="overwrite existing transcript")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    root = Path(args.asr_root)
    state = probe(root)
    if args.probe:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state["ok"] else 3
    if not state["ok"]:
        print(json.dumps({"ok": False, "error": "asr_backend_not_ready",
                          "probe": state}, ensure_ascii=False, indent=2))
        return 2
    if not args.workdir:
        print(json.dumps({"ok": False, "error": "workdir_required"},
                         ensure_ascii=False, indent=2))
        return 2

    workdir = Path(args.workdir)
    source = workdir / "source"
    source.mkdir(parents=True, exist_ok=True)
    out_md = source / "transcript.md"
    if out_md.is_file() and not args.force:
        ok, _ = looks_like_transcript(out_md.read_text(encoding="utf-8"))
        if ok:
            print(json.dumps({"ok": True, "skipped": True,
                              "reason": "valid transcript.md already exists (use --force)",
                              "transcript": str(out_md)}, ensure_ascii=False, indent=2))
            return 0

    status_file = workdir / "status.json"
    status = {}
    if status_file.is_file():
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            status = {}
    video_id = str(status.get("video_id", ""))

    audio = Path(args.audio) if args.audio else None
    if audio is None:
        api = workdir / "api"
        for name in (f"{video_id}.mp3", f"{video_id}.mp4"):
            if (api / name).is_file():
                audio = api / name
                break
    if audio is None or not audio.is_file():
        print(json.dumps({"ok": False, "error": "audio_not_found",
                          "hint": "pass --audio or put <id>.mp3/.mp4 under workdir/api/"},
                         ensure_ascii=False, indent=2))
        return 2

    wav_path = source / "asr-16k.wav"
    t0 = time.time()
    try:
        conv = to_wav(audio, wav_path)
        samples = read_samples(wav_path)
        vad_path = Path(state["paths"]["vad"])
        if vad_path.is_file():
            try:
                segments = vad_segments(samples, vad_path)
                seg_mode = "silero VAD"
            except Exception:  # noqa: BLE001 - fall back, never lose the transcript
                segments = split_segments(samples)
                seg_mode = "energy silence (VAD init failed)"
        else:
            segments = split_segments(samples)
            seg_mode = "energy silence"
        if not segments:
            raise RuntimeError("no speech segments produced")

        sys.path.insert(0, str(root / "site"))
        import sherpa_onnx  # noqa: PLC0415
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=state["paths"]["model"],
            tokens=state["paths"]["tokens"],
            num_threads=args.threads,
            use_itn=True,
            language="zh",
            debug=False,
        )
        lines = []
        for a, b in segments:
            stream = recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples[a:b])
            recognizer.decode_stream(stream)
            text = (stream.result.text or "").strip()
            if text and usable_text(text):
                lines.append(f"[{fmt_ts(a / SAMPLE_RATE)},{fmt_ts(b / SAMPLE_RATE)}] {text}")

        duration_s = round(len(samples) / SAMPLE_RATE, 2)
        elapsed = round(time.time() - t0, 2)
        header = [
            f"# {video_id or audio.stem} · 本地离线转写（sherpa-onnx SenseVoice）",
            "",
            f"> 引擎：{state['engine']}（sherpa-onnx {state['engine_version']}），CPU 推理",
            f"> 模型：`{state['paths']['model']}`（int8，{state['model_size']:,} bytes）",
            f"> 音频：`{audio}` → ffmpeg 16 kHz 单声道 PCM（{duration_s} 秒）",
            f"> 分段：{seg_mode}，共 {len(segments)} 段，{len(lines)} 段有可用文字（已滤除标点/单字误识别）",
            f"> 生成时间：{datetime.now().isoformat(timespec='seconds')} · 耗时 {elapsed} 秒",
            "> 说明：离线 ASR 存在听写误差，产品名 / 数字 / 人名按听写结果原样保留、使用前需核对；本脚本不猜测、不改写。",
            "",
        ]
        out_md.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
        ok, why = looks_like_transcript(out_md.read_text(encoding="utf-8"))

        report = {
            "ok": ok, "transcript_valid": why,
            "engine": state["engine"], "engine_version": state["engine_version"],
            "model": state["paths"]["model"], "model_size": state["model_size"],
            "audio": str(audio), "audio_sha256": hashlib.sha256(
                audio.read_bytes()[:4 * 1024 * 1024]).hexdigest(),
            "wav": conv, "duration_s": duration_s, "segmentation": seg_mode,
            "segments": len(segments), "segments_with_text": len(lines),
            "transcript": str(out_md), "transcript_bytes": out_md.stat().st_size,
            "elapsed_s": elapsed, "threads": args.threads,
        }
        (source / "local-asr.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        merge_status(status_file, local_asr=report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if ok else 4
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": "asr_failed", "detail": str(exc)[:500],
                  "probe": state}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
