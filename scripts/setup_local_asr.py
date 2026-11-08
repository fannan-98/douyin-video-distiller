#!/usr/bin/env python3
"""Idempotent, portable installer for the offline ASR backend.

Why this exists
---------------
v1.7 shipped the offline backend as something that already happened to exist on
one machine (``$WORKBUDDY_ASR_ROOT``).  Moving to another machine meant re-deriving
a dozen shell incantations, and there was no way to tell a good install from a
half-finished one.  This script makes the deployment reproducible and
self-verifying.

The design is borrowed from two places rather than invented here:

* ``mm7h/XiaoZhi.Net`` ``scripts/Download-SherpaModels.ps1`` — skip when the
  target is complete, **refuse** when it is partial (so a good model is never
  silently clobbered by a bad download), require an explicit ``--force`` to
  redo, and run capability checks before the first byte is transferred.
* ``k2-fsa/sherpa-onnx`` official model releases — the SenseVoice tarball plus
  ``silero_vad.onnx`` are the two upstream artefacts, so the pinned sizes and
  SHA256 digests below are the integrity contract.

Commands
--------
    python setup_local_asr.py --check          # report only, transfer nothing
    python setup_local_asr.py                  # idempotent install
    python setup_local_asr.py --verify         # re-check installed digests
    python setup_local_asr.py --force          # re-download everything

Exit codes: 0 = ready, 3 = not ready (partial/missing), 1 = hard failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from dy_config import ASR_ROOT

DEFAULT_ROOT = ASR_ROOT
ENGINE_PKG = "sherpa-onnx==2.0.1"
NUMPY_PKG = "numpy>=2.1"

# Local HTTP proxies worth trying, in order.  This machine runs a proxy on 10810
# whose registry entry breaks pip's TLS; the *environment* proxy on 47977 502s
# for some hosts.  curl is driven explicitly with -x so neither default wins.
PROXY_CANDIDATES = ["http://127.0.0.1:10810"]

SENSEVOICE_TARBALL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"

# Pinned artefacts: (relative path, size, sha256, [candidate URLs])
# Digests were captured from the known-good install on 2026-11-08 and are the
# contract a fresh machine must reproduce.
ARTEFACTS = [
    {
        "rel": "models/sense-voice/model.int8.onnx",
        "size": 239233841,
        "sha256": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        "member": "model.int8.onnx",
        "urls": [
            "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx",
            "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx",
            f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{SENSEVOICE_TARBALL}",
        ],
        "tarball": f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{SENSEVOICE_TARBALL}",
    },
    {
        "rel": "models/sense-voice/tokens.txt",
        "size": 315894,
        "sha256": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        "member": "tokens.txt",
        "urls": [
            "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt",
            "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt",
            f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{SENSEVOICE_TARBALL}",
        ],
        "tarball": f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{SENSEVOICE_TARBALL}",
    },
    {
        "rel": "models/sense-voice/silero_vad.onnx",
        "size": 643854,
        "sha256": "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        "member": "silero_vad.onnx",
        "urls": [
            "https://hf-mirror.com/csukuangfj/sherpa-onnx-vad-models/resolve/main/silero_vad.onnx",
            "https://huggingface.co/csukuangfj/sherpa-onnx-vad-models/resolve/main/silero_vad.onnx",
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
        ],
        "tarball": "",
    },
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def sha256(path: Path, *, limit: int = 0) -> str:
    """Digest a file. ``limit`` > 0 hashes only the first N bytes (fast probe)."""
    h = hashlib.sha256()
    read = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            if limit and read + len(chunk) > limit:
                chunk = chunk[: limit - read]
            h.update(chunk)
            read += len(chunk)
            if limit and read >= limit:
                break
    return h.hexdigest()


def free_gib(path: Path) -> float:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / (1 << 30)
    except OSError:
        return -1.0


def curl_fetch(url: str, dest: Path, *, resume: bool = True) -> dict:
    """Download with the machine's real network topology.

    Environment proxies are stripped (they 502 for some hosts and break TLS in
    this sandbox) and each candidate proxy is tried explicitly.  ``--ssl-no-revoke``
    is required because the local schannel cannot reach the CRL endpoints.
    """
    curl = shutil.which("curl") or "curl"
    attempts = []
    proxies = [None] + PROXY_CANDIDATES
    for proxy in proxies:
        cmd = [curl, "--ssl-no-revoke", "-L", "--fail", "--retry", "4",
               "--retry-delay", "2", "--connect-timeout", "20",
               "-o", str(dest)]
        if resume:
            cmd += ["-C", "-"]
        if proxy:
            cmd += ["-x", proxy]
        cmd.append(url)
        env = {k: v for k, v in os.environ.items()
               if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)
        attempts.append({"proxy": proxy or "direct", "exit": proc.returncode,
                         "stderr": proc.stderr.strip()[-300:]})
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return {"ok": True, "url": url, "proxy": proxy or "direct",
                    "bytes": dest.stat().st_size, "attempts": attempts}
    return {"ok": False, "url": url, "attempts": attempts}


def pip_install(root: Path, packages: list[str]) -> dict:
    site = root / "site"
    site.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update({
        "PIP_CACHE_DIR": str(root / "pip-cache"),
        "NO_PROXY": "*", "no_proxy": "*",
        "PYTHONIOENCODING": "utf-8",
    })
    cmd = [sys.executable, "-m", "pip", "install", "--no-input",
           "--disable-pip-version-check", "--no-warn-script-location",
           "--target", str(site), *packages]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    return {"cmd": " ".join(cmd), "exit": proc.returncode,
            "tail": (proc.stdout or proc.stderr).strip()[-800:]}


def extract_member(tarball: Path, member: str, dest: Path) -> bool:
    """Pull one file out of the SenseVoice tarball using stdlib bz2 support.

    Windows' bundled ``tar.exe`` sometimes lacks bzip2; Python's tarfile does not
    have that problem, so extraction never depends on the host's tar build.
    """
    try:
        with tarfile.open(tarball, "r:bz2") as tf:
            for info in tf:
                if Path(info.name).name == member and info.isfile():
                    src = tf.extractfile(info)
                    if src is None:
                        return False
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    return True
    except (tarfile.TarError, OSError, EOFError):
        return False
    return False


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #

def preflight(root: Path) -> list[dict]:
    checks = []

    py_ok = sys.version_info >= (3, 9)
    checks.append({"name": "python>=3.9", "ok": py_ok,
                   "detail": sys.version.split()[0]})

    ffmpeg = shutil.which("ffmpeg") or ""
    checks.append({"name": "ffmpeg on PATH", "ok": bool(ffmpeg),
                   "detail": ffmpeg or "needed to convert mp4/mp3 -> 16 kHz wav"})

    free = free_gib(root)
    need = 0.6  # site ~110 MiB + models ~230 MiB + cache headroom
    checks.append({"name": "disk free >= 0.6 GiB", "ok": free >= need,
                   "detail": f"{free:.2f} GiB free on {root.drive or root}"})

    writable = True
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        writable = False
        checks.append({"name": "target writable", "ok": False, "detail": str(exc)[:120]})
    if writable:
        checks.append({"name": "target writable", "ok": True, "detail": str(root)})
    return checks


def audit(root: Path) -> dict:
    """Classify every artefact: present+correct / missing / corrupt."""
    rows = []
    for art in ARTEFACTS:
        path = root / art["rel"]
        row = {"rel": art["rel"], "path": str(path), "expected_size": art["size"],
               "expected_sha256": art["sha256"]}
        if not path.is_file():
            row.update(state="missing", actual_size=0)
        else:
            size = path.stat().st_size
            row["actual_size"] = size
            if size != art["size"]:
                row["state"] = "partial" if size < art["size"] else "size_mismatch"
            else:
                digest = sha256(path)
                row["state"] = "ok" if digest == art["sha256"] else "digest_mismatch"
                row["actual_sha256"] = digest
        rows.append(row)

    site = root / "site"
    module_ok = (site / "sherpa_onnx" / "__init__.py").is_file()
    numpy_ok = (site / "numpy" / "__init__.py").is_file()
    engine_version = ""
    if module_ok:
        code = ("import sys;sys.path.insert(0,%r);import sherpa_onnx;"
                "print(getattr(sherpa_onnx,'__version__',''))" % str(site))
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        engine_version = proc.stdout.strip()
    return {
        "root": str(root),
        "artefacts": rows,
        "site": {"path": str(site), "module": module_ok, "numpy": numpy_ok,
                 "engine_version": engine_version},
        "ready": all(r["state"] == "ok" for r in rows) and module_ok and numpy_ok,
    }


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #

def install(root: Path, *, verify_only: bool, force: bool,
            skip_engine: bool) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    report: dict = {"root": str(root), "at": datetime.now().isoformat(timespec="seconds"),
                    "mode": "verify" if verify_only else "install",
                    "force": force, "steps": []}
    before = audit(root)

    if verify_only:
        report.update({"ok": before["ready"], "audit": before})
        return report

    # --- python packages ---------------------------------------------------
    if skip_engine:
        report["steps"].append({"step": "pip", "skipped": "--skip-engine"})
    elif before["site"]["module"] and before["site"]["numpy"] and not force:
        report["steps"].append({"step": "pip", "skipped": "already importable",
                                "engine_version": before["site"]["engine_version"]})
    else:
        res = pip_install(root, [ENGINE_PKG, NUMPY_PKG])
        report["steps"].append({"step": "pip", **res})
        if res["exit"] != 0:
            report.update({"ok": False, "audit": audit(root),
                           "error": "pip install failed"})
            return report

    # --- model artefacts ---------------------------------------------------
    for art in ARTEFACTS:
        path = root / art["rel"]
        state = next(r["state"] for r in before["artefacts"] if r["rel"] == art["rel"])
        if state == "ok" and not force:
            report["steps"].append({"step": "model", "rel": art["rel"],
                                    "skipped": "already complete + digest match"})
            continue
        if state in ("partial", "size_mismatch", "digest_mismatch") and not force:
            # Refuse rather than overwrite: a truncated download must not eat a
            # good neighbouring file, and a silent retry hides the real cause.
            report["steps"].append({
                "step": "model", "rel": art["rel"], "refused": True,
                "state": state,
                "hint": "file is corrupt/partial; rerun with --force to replace it"})
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        if tmp.exists():
            tmp.unlink()
        got = None
        for url in art["urls"]:
            is_tar = url.endswith(".tar.bz2")
            target = tmp if not is_tar else root / "cache" / SENSEVOICE_TARBALL
            target.parent.mkdir(parents=True, exist_ok=True)
            res = curl_fetch(url, target, resume=not is_tar)
            if not res["ok"]:
                report["steps"].append({"step": "download", "rel": art["rel"],
                                        "url": url, "ok": False,
                                        "attempts": res["attempts"]})
                continue
            if is_tar:
                if not extract_member(target, art["member"], tmp):
                    report["steps"].append({"step": "extract", "rel": art["rel"],
                                            "url": url, "ok": False,
                                            "error": f"member {art['member']} not in tarball"})
                    continue
            size = tmp.stat().st_size
            digest = sha256(tmp)
            if size == art["size"] and digest == art["sha256"]:
                tmp.replace(path)
                got = {"url": url, "proxy": res.get("proxy"), "size": size,
                       "sha256": digest}
                break
            report["steps"].append({
                "step": "download", "rel": art["rel"], "url": url, "ok": False,
                "error": "size/digest mismatch", "size": size, "sha256": digest,
                "expected_size": art["size"]})
            tmp.unlink(missing_ok=True)
        if got:
            report["steps"].append({"step": "model", "rel": art["rel"], "ok": True, **got})
        else:
            report["steps"].append({"step": "model", "rel": art["rel"], "ok": False,
                                    "error": "all sources failed"})

    after = audit(root)
    report["audit"] = after
    report["ok"] = after["ready"]
    if report["ok"]:
        (root / "install-manifest.json").write_text(
            json.dumps({"engine": ENGINE_PKG, "numpy": NUMPY_PKG,
                        "artefacts": [{"rel": a["rel"], "size": a["size"],
                                       "sha256": a["sha256"]} for a in ARTEFACTS],
                        "installed_at": report["at"]},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Portable offline ASR installer")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--check", action="store_true", help="report only, transfer nothing")
    ap.add_argument("--verify", action="store_true", help="re-check digests of an existing install")
    ap.add_argument("--force", action="store_true", help="re-download even if complete")
    ap.add_argument("--skip-engine", action="store_true", help="models only")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    root = Path(args.root)
    if args.check:
        payload = {"mode": "check", "root": str(root),
                   "preflight": preflight(root), "audit": audit(root)}
        payload["ok"] = (all(c["ok"] for c in payload["preflight"])
                         and payload["audit"]["ready"])
        code = 0 if payload["ok"] else 3
    else:
        payload = install(root, verify_only=args.verify, force=args.force,
                          skip_engine=args.skip_engine)
        code = 0 if payload.get("ok") else 3

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
