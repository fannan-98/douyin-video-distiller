"""scan-search 的回归：全站搜索结果按作者入队 + 去重。

`watch.enqueue_search_videos` 接收 search_discover.py 产出的 JSON 列表，每条
视频带自己的 author_sec_uid；它要：① 按 seen.json 去重 ② 没有有效作者
sec_uid 的条目必须跳过（否则会污染台账）③ 自动补齐作者账号行并打
discovered_via=search 标记。

Run: python tests/test_scan_search.py
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import watch  # noqa: E402

SEC_A = "MS4wLjABAAAAQUFn-KODXUGHd6PGbfJEmmzCIu3C1vzQBPgRprrkpfBMwWAXm09Vf48yEpik6k2W"
SEC_B = "MS4wLjABAAAAxyz-KODXUGHd6PGbfJEmmzCIu3C1vzQBPgRprrkpfBMwWAXm09Vf48yEpik6k2B"

VALID = [
    {"video_id": "1111111111111111111", "title": "靠信息差月入过万", "views": "1.2万",
     "author_sec_uid": SEC_A, "author_nickname": "老王说钱",
     "web_url": "https://www.douyin.com/video/1111111111111111111",
     "create_time": 1762000000, "likes": 3200, "comments": 88},
    {"video_id": "2222222222222222222", "title": "副业实操三条路", "views": "8800",
     "author_sec_uid": SEC_B, "author_nickname": "小美创业",
     "web_url": "https://www.douyin.com/video/2222222222222222222",
     "create_time": 1762100000, "likes": 1500, "comments": 40},
    {"video_id": "3333333333333333333", "title": "同一作者第二弹", "views": "990",
     "author_sec_uid": SEC_A, "author_nickname": "老王说钱",
     "web_url": "https://www.douyin.com/video/3333333333333333333",
     "create_time": 1762200000, "likes": 700, "comments": 12},
]

BAD_AUTHOR = [
    {"video_id": "4444444444444444444", "title": "作者 sec_uid 非法", "views": "",
     "author_sec_uid": "not-a-real-sec-uid", "author_nickname": "幽灵",
     "create_time": 1762300000, "likes": 1, "comments": 0},
]


def _reset(tmp):
    watch.WATCH_ROOT = tmp
    watch.ACCOUNTS = tmp / "accounts.json"
    watch.SEEN = tmp / "seen.json"
    watch.QUEUE = tmp / "queue.json"


def test_enqueue_basic_and_accounts(tmp_path):
    _reset(tmp_path)
    r = watch.enqueue_search_videos(VALID)
    assert r["new"] == 3, r
    assert r["already_seen"] == 0
    assert r["skipped"] == 0
    q = json.loads(watch.QUEUE.read_text(encoding="utf-8"))
    assert len(q["items"]) == 3
    # 两个作者都建了账号行
    accs = json.loads(watch.ACCOUNTS.read_text(encoding="utf-8"))
    secs = {a["sec_uid"] for a in accs["accounts"]}
    assert SEC_A in secs and SEC_B in secs
    assert all(a.get("discovered_via") == "search" for a in accs["accounts"])
    # 字段透传
    by_id = {i["aweme_id"]: i for i in q["items"]}
    assert by_id["1111111111111111111"]["title_hint"] == "靠信息差月入过万"
    assert by_id["1111111111111111111"]["create_time"] == 1762000000
    assert by_id["1111111111111111111"]["likes"] == 3200
    assert by_id["1111111111111111111"]["discovered_via"] == "search"
    assert by_id["1111111111111111111"]["account"] == SEC_A


def test_enqueue_dedup_second_pass(tmp_path):
    _reset(tmp_path)
    watch.enqueue_search_videos(VALID)
    # 重复一遍，加一条新视频
    again = VALID + [
        {"video_id": "5555555555555555555", "title": "全新作者新视频", "views": "10万",
         "author_sec_uid": SEC_B, "author_nickname": "小美创业",
         "create_time": 1762400000, "likes": 9000, "comments": 300},
    ]
    r = watch.enqueue_search_videos(again)
    assert r["new"] == 1, r
    assert r["already_seen"] == 3, r
    q = json.loads(watch.QUEUE.read_text(encoding="utf-8"))
    assert len(q["items"]) == 4


def test_enqueue_skips_bad_author(tmp_path):
    _reset(tmp_path)
    r = watch.enqueue_search_videos(BAD_AUTHOR)
    assert r["new"] == 0
    assert r["skipped"] == 1, r
    q = json.loads(watch.QUEUE.read_text(encoding="utf-8"))
    assert len(q["items"]) == 0, "非法作者的视频绝不可入队"


def test_cmd_scan_search_via_json(tmp_path):
    _reset(tmp_path)
    jf = tmp_path / "search.json"
    jf.write_text(json.dumps(VALID, ensure_ascii=False), encoding="utf-8")
    args = types.SimpleNamespace(json=str(jf))
    assert watch.cmd_scan_search(args) == 0
    q = json.loads(watch.QUEUE.read_text(encoding="utf-8"))
    assert len(q["items"]) == 3


if __name__ == "__main__":
    import tempfile
    import pathlib
    funcs = sorted((v for k, v in globals().items()
                    if k.startswith("test_") and callable(v)),
                   key=lambda f: f.__name__)
    passed = failed = 0
    for f in funcs:
        try:
            names = f.__code__.co_varnames[:f.__code__.co_argcount]
            if names and names[0] in ("tmp_path", "tmp"):
                with tempfile.TemporaryDirectory() as d:
                    f(pathlib.Path(d))
            else:
                f()
            print(f"PASS {f.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {f.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
