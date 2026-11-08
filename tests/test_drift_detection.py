"""Regression: same-size edits must not be mistaken for a published note.

v1.10 compared `verified_file_size` against the file size, which is Nextflow's
`cache 'lenient'` and inherits its documented blind spot: a file rewritten to
the same length looks untouched.  `derive_state` now hashes content.

Note on newline: `Path.write_text` performs newline translation on Windows, so
`\n` becomes `\r\n` on disk and the digest no longer matches what the caller
computed from `body.encode()`.  Every write here passes newline="" to keep the
fixture byte-exact — the first draft of this test "failed" purely because of it.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import watch  # noqa: E402

PRE = "# 标题\n\n正文第一段。\n"
POST_OLD = "结尾一句话。\n"
POST_NEW = "结尾两句话！\n"          # 与 POST_OLD 等长（UTF-8 下同为 21 bytes）


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(root: Path, body: str, *, sha_for: str | None = None,
          size_for: int | None = None, verified: bool | None = True,
          with_sha: bool = True) -> Path:
    """Fake a run dir whose status.json records `sha_for` / `size_for`."""
    (root / "source").mkdir(parents=True, exist_ok=True)
    (root / "api").mkdir(parents=True, exist_ok=True)
    note = root / "【WorkBuddy-2026-11-08】999-note.md"
    note.write_text(body, encoding="utf-8", newline="")   # no \n -> \r\n
    real_bytes = len(body.encode("utf-8"))
    want_bytes = size_for if size_for is not None else real_bytes
    want_sha = sha_for if sha_for is not None else digest(body)

    ima = {"put_status": 200, "verified_file_size": want_bytes}
    rec = {"bytes": want_bytes}
    if with_sha:
        ima["verified_note_sha256"] = want_sha
        rec["sha256"] = want_sha
    if verified is not None:
        ima["verified"] = verified
    (root / "status.json").write_text(
        json.dumps({"ima": ima, "note": rec}, ensure_ascii=False),
        encoding="utf-8", newline="")
    (root / "lint-note.json").write_text(
        json.dumps({"ok": True}), encoding="utf-8", newline="")
    return root


def show(label: str, root: Path) -> tuple[str, str]:
    state, why = watch.derive_state({"run_dir": str(root), "status": "queued"})
    print(f"  {label}\n     -> {state}  |  {why}")
    return state, why


CASES = []
tmp = Path(tempfile.mkdtemp(prefix="dy-drift-"))
old_body = PRE + POST_OLD
new_body = PRE + POST_NEW
assert len(old_body.encode()) == len(new_body.encode()), "用例本身必须等长"
old_sha = digest(old_body)

print("== 1. 等长改写（本次 bug 的形状）==")
show("磁盘=new，记录=old，字节数完全相同",
     build(tmp / "c1", new_body, sha_for=old_sha))
CASES.append(("等长改写必须 drift",
              watch.derive_state({"run_dir": str(tmp / "c1")})[0] == "distilled"
              and "已被改写" in watch.derive_state({"run_dir": str(tmp / "c1")})[1]))

print("\n== 2. 完全一致 ==")
st, why = show("sha 与磁盘一致", build(tmp / "c2", old_body))
CASES.append(("一致时必须 published", st == "published" and "一致" in why))

print("\n== 3. 旧记录只有 size、size 相等（历史数据，无法判定内容）==")
st, why = show("无 sha256，仅 size 相等",
               build(tmp / "c3", new_body, size_for=len(old_body.encode()),
                     with_sha=False))
CASES.append(("无 sha 时应说明「仅按大小比对」而非静默通过",
              st == "published" and "仅按大小比对" in why))

print("\n== 4. 旧记录只有 size、size 不等 ==")
st, why = show("无 sha256，size 不等",
               build(tmp / "c4", new_body,
                     size_for=len(old_body.encode()) + 40, with_sha=False))
CASES.append(("size 不等必须 drift", st == "distilled" and "已变" in why))

print("\n== 5. legacy run：verified 字段缺失，只有 verified_file_size ==")
st, why = show("legacy 指纹不匹配",
               build(tmp / "c5", new_body,
                     size_for=len(old_body.encode()) + 7,
                     verified=None, with_sha=False))
CASES.append(("legacy 不匹配也必须 drift，不能退回 lint PASS",
              st == "distilled" and ("已变" in why or "改写" in why)))

print("\n== 6. verified=False，但 PUT 200 且指纹一致 ==")
st, why = show("未标 verified，靠 PUT+指纹兜底",
               build(tmp / "c6", old_body, verified=False))
CASES.append(("PUT 200 + 指纹一致 判 published", st == "published"))

print("\n== 7. 从未上传（status.json 无 ima 段）==")
root7 = tmp / "c7"
(root7 / "source").mkdir(parents=True, exist_ok=True)
(root7 / "note.md").write_text(old_body, encoding="utf-8", newline="")
(root7 / "status.json").write_text(json.dumps({"video_id": "999"}),
                                  encoding="utf-8", newline="")
(root7 / "lint-note.json").write_text(json.dumps({"ok": True}),
                                      encoding="utf-8", newline="")
st, why = show("无 ima 记录", root7)
CASES.append(("无指纹时判 distilled 而非 published", st == "distilled"))

print("\n" + "=" * 62)
ok = True
for label, passed in CASES:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    ok = ok and passed
print("=" * 62)
print("RESULT:", "ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
