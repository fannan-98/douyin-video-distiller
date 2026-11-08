"""Regression: one scan must not queue the same video twice.

`seen.json` only knows about *previous* scans, so a duplicate inside a single
`--items` payload went straight into the queue.  That is not hypothetical: a
pinned video is rendered in more than one group on a profile page, and the agent
reading the DOM can also pick the same card up twice.  The cost is a second
download (~100 MB, ~90 s of transcription) and a second note for the same video.

Run: python tests/test_scan_queue.py
"""
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import watch  # noqa: E402

A = "7123456789012345678"
B = "7123456789012345679"
SEC = "MS4wLjABAAAA" + "x" * 40


def fresh_ledger() -> Path:
    tmp = Path(tempfile.mkdtemp())
    watch.WATCH_ROOT = tmp
    watch.ACCOUNTS, watch.SEEN = tmp / "accounts.json", tmp / "seen.json"
    watch.QUEUE, watch.INBOX = tmp / "queue.json", tmp / "inbox.txt"
    watch.save(watch.ACCOUNTS, {"accounts": [
        {"sec_uid": SEC, "nickname": "测试号", "last_scan_at": "", "last_new_count": 0}]})
    return tmp


def scan(items: str) -> dict:
    args = types.SimpleNamespace(account=SEC, nickname="", ids="", json=True, items=items)
    watch.cmd_scan(args)
    return json.loads(watch.QUEUE.read_text(encoding="utf-8"))


def main() -> int:
    failed = 0

    def check(name: str, got, want) -> None:
        nonlocal failed
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failed += 1
            print(f"       期望 {want}  实际 {got}")

    # 1) 同一批里重复两次
    fresh_ledger()
    q = scan(f"{A}~标题甲~1.2万|{A}~标题甲~1.2万|{B}~标题乙~879")
    check("本批重复只入队一次", [i["aweme_id"] for i in q["items"]], [A, B])

    # 2) 重复时保留信息更全的那一份
    fresh_ledger()
    q = scan(f"{A}~~|{A}~标题甲~1.2万")
    check("重复记录取更全的一条", (q["items"][0]["title_hint"], q["items"][0]["views_hint"]),
          ("标题甲", "1.2万"))

    # 3) 跨扫描重复仍靠 seen.json 拦
    fresh_ledger()
    scan(f"{A}~标题甲~1.2万")
    q = scan(f"{A}~标题甲~1.2万|{B}~标题乙~879")
    check("跨扫描不重复入队", [i["aweme_id"] for i in q["items"]], [A, B])

    # 4) 报表要能看出被折叠了几条，否则「扫了 20 条只新 5 条」说不清原因
    fresh_ledger()
    args = types.SimpleNamespace(account=SEC, nickname="", ids="", json=True,
                                 items=f"{A}~a~1|{A}~a~1|{B}~b~1")
    watch.cmd_scan(args)
    seen = json.loads(watch.SEEN.read_text(encoding="utf-8"))
    check("seen 只登记唯一 id", sorted(seen["ids"]), sorted([A, B]))

    print()
    print("ALL PASS" if not failed else f"{failed} 条失败")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
