"""Regression: douyin titles must not silently swallow videos.

`--items` is typed by the agent out of a DOM snapshot, in the format
``id~标题~播放量|id~标题~播放量``.  v1.12 split on ``|`` first and ``~`` second:

* a title containing ``|`` produced an id-less chunk which was skipped with no
  message - the video never reached the queue and nothing looked wrong;
* a title containing ``~`` pushed the view count out of index 1, so the video
  entered with a truncated title and no view hint.

Both are silent.  A queue that quietly drops rows is worse than one that
crashes: the morning brief simply shows fewer videos than were published, and
nobody can tell whether that means "he posted less" or "we lost it".

Run: python tests/test_scan_items.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import watch  # noqa: E402

A = "7123456789012345678"
B = "7123456789012345679"
C = "7123456789012345680"

CASES = [
    # (说明, 输入, 期望的 [(id, 标题, 播放量), ...])
    (
        "基准：两条正常条目",
        f"{A}~标题甲~1.2万|{B}~标题乙~879",
        [(A, "标题甲", "1.2万"), (B, "标题乙", "879")],
    ),
    (
        "标题里带 | ：必须并回上一条，不能丢视频",
        f"{A}~上半年账|下半年怎么做~1.2万|{B}~标题乙~879",
        [(A, "上半年账|下半年怎么做", "1.2万"), (B, "标题乙", "879")],
    ),
    (
        "标题里带 ~ ：播放量仍要取到尾部，标题完整保留",
        f"{A}~A~B~C~3.4万",
        [(A, "A~B~C", "3.4万")],
    ),
    (
        "带「共创 / 置顶」前缀的播放量",
        f"{A}~标题~共创1.5万|{B}~标题~置顶42.0万",
        [(A, "标题", "共创1.5万"), (B, "标题", "置顶42.0万")],
    ),
    (
        "没有播放量：标题不能把数字当播放量吃掉",
        f"{A}~2026 选品思路",
        [(A, "2026 选品思路", "")],
    ),
    (
        "只有 id：标题与播放量留空",
        A,
        [(A, "", "")],
    ),
    (
        "垃圾块不该产生条目，也不该污染前一条",
        f"{A}~标题甲~1.2万||~~",
        [(A, "标题甲", "1.2万")],
    ),
    (
        "续块没有播放量：只拼标题，原播放量保留",
        f"{A}~前半|后半",
        [(A, "前半|后半", "")],
    ),
    (
        "空串不炸",
        "",
        [],
    ),
]


def main() -> int:
    failed = 0
    for name, raw, want in CASES:
        got = watch.parse_scan_items(raw)
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failed += 1
            print(f"       输入: {raw!r}")
            print(f"       期望: {want}")
            print(f"       实际: {got}")
    print()
    print(f"{len(CASES) - failed}/{len(CASES)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
