#!/usr/bin/env python3
"""One-command readiness check for a fresh clone.

    python scripts/doctor.py

Prints the effective configuration, checks Python version, writable paths, the
required ima knowledge base id, and optional dependencies.  Never writes files.
Exit code 0 = the main pipeline is ready; 1 = something needs your attention.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

try:
    import dy_config as cfg
except ImportError:  # allow `python -m scripts.doctor` from the repo root
    from scripts import dy_config as cfg

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def main() -> int:
    kb_ok = bool(cfg.IMA_KB_ID) and not cfg.IMA_KB_ID.lower().startswith("your_")

    print("douyin-video-distiller · 配置体检")
    print("=" * 68)
    print(f"  Python              {sys.version.split()[0]}")
    print(f"  Executable          {sys.executable}")
    print("-" * 68)
    for key, value in cfg.summary().items():
        marker = FAIL if key == "IMA_KB_ID" and not kb_ok else OK
        print(f"{marker} {key:22} {value}")
    print("-" * 68)

    problems: list[str] = []
    if sys.version_info < (3, 10):
        problems.append("Python 需要 3.10+（推荐 3.13）")
    if not kb_ok:
        problems.append(
            "IMA_KB_ID 未设置或仍是示例占位值：阶段 4/5 入库不可用，见 .env.example"
        )

    # Writability: walk up to the nearest existing ancestor, never create dirs.
    for label, path in (("产物根目录", cfg.DISTILL_ROOT), ("ASR 根目录", cfg.ASR_ROOT)):
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if probe.exists() and not os.access(probe, os.W_OK):
            problems.append(f"{label}不可写: {path}")

    print("可选组件：")
    ffmpeg = shutil.which("ffmpeg")
    print(f"  {OK if ffmpeg else WARN} ffmpeg                "
          f"{ffmpeg or '未安装（仅本地 ASR 转写需要）'}")
    sherpa = importlib.util.find_spec("sherpa_onnx") is not None
    print(f"  {OK if sherpa else WARN} sherpa-onnx          "
          f"{'已安装' if sherpa else '未安装（可选，setup_local_asr.py --check）'}")
    vendor_ok = cfg.VENDOR_DIR.is_dir()
    print(f"  {OK if vendor_ok else WARN} vendored 搜索引擎    "
          f"{cfg.VENDOR_DIR}{'' if vendor_ok else '  (可选，未安装)'}")

    required = ["run_pipeline.py", "douyin_detail.py", "lint_note.py",
                "dy_config.py", "doctor.py"]
    missing = [name for name in required if not (cfg.SCRIPTS_DIR / name).is_file()]
    print(f"  {OK if not missing else FAIL} 核心脚本            "
          f"{'齐全' if not missing else '缺失: ' + ', '.join(missing)}")
    if missing:
        problems.append(f"核心脚本缺失: {', '.join(missing)}")

    print("=" * 68)
    if problems:
        for problem in problems:
            print(f"{FAIL} {problem}")
        print("\n说明：主链路（解析 → 下载/转写 → 蒸馏 → lint）不依赖上面的可选组件；")
        print("      阶段 4 入库需要 WorkBuddy + ima MCP（token 在宿主进程，脚本不持有）。")
        return 1
    print(f"{OK} 配置就绪。把本目录作为 WorkBuddy skill 加载，贴一条抖音链接即可。")
    print("     阶段 4 入库仍需 WorkBuddy + ima MCP，见 README「边界」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
