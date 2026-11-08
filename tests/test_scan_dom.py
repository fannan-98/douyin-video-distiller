"""extract_works_from_dom + scan-dom 的回归。

DOM 文本来自 2026-11-08 在 *登录态* Edge 上抓到的真实账号主页快照
（nickname「这很容易」，页头「作品23」）。该快照覆盖了所有已知卡片形态：

* 播放量前缀：置顶42.0万 / 共创1.5万 / 1937（纯数字）/ 1.6万 / 11.3万
* 标题在 <a> 文字里整段重复两遍（#话题序列出现两次）
* 链接是相对路径 /video/...（非 https://...）

游客态下同一快照只会拿到 ~1 条（被扫码弹窗遮挡），所以这些是登录态的数据。
"""
import os
import sys
import json
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import watch  # noqa: E402

DOM = """<a node_id=34 href="/video/7682633651724569891">置顶42.0万抄底拼多多，A轮入宁德，24 岁带头举牌上市公司 宇宙E-BIKE 创始人程泓宁的逆向投资之路 #这很容易 #抖来聊聊 #视频播客扶持计划 #投资#AI新星计划抄底拼多多，A轮入宁德，24 岁带头举牌上市公司 宇宙E-BIKE 创始人程泓宁的逆向投资之路 #这很容易 #抖来聊聊 #视频播客扶持计划 #投资</a>
<a node_id=35 href="/video/7625297188536454434">置顶39.0万23岁经济自由，真正在股票市场赚到钱的年轻人，都是如何思考的 #股市 #投资 #抖来聊聊 #视频播客扶持计划 #ai新星计划23岁经济自由，真正在股票市场赚到钱的年轻人，都是如何思考的 #股市 #投资 #抖来聊聊 #视频播客扶持计划 #ai新星计划</a>
<a node_id=36 href="/video/7653082970223398190">置顶18.9万我帮公司搭了套AI自动化系统，然后被公司裁员了… 聊聊AI到来后的职场现状#抖来聊聊 #AI新星计划 #视频播客扶持计划 #抖音前沿科技首发计划 #这很容易我帮公司搭了套AI自动化系统，然后被公司裁员了… 聊聊AI到来后的职场现状#抖来聊聊 #AI新星计划 #视频播客扶持计划 #抖音前沿科技首发计划 #这</a>
<a node_id=37 href="/video/7705023283414027561">1937月薪四千到经营十亿，女总裁归零后如何再起？ @奔跑吧dita｜双11真破价到底#这很容易#轻氧#访谈#抖来聊聊月薪四千到经营十亿，女总裁归零后如何再起？ @奔跑吧dita｜双11真破价到底#这很容易#轻氧#访谈#抖来聊聊</a>
<a node_id=38 href="/video/7699082931108646159">共创1.5万这是场耗资一个亿的复盘！ 我们将通过李潇这笔天价学费，拆解品牌创业的底层逻辑，拆解瑞幸古茗能够突出重围的核心方法论 #这很容易 #咖啡 #李潇 #抖来聊聊 #视频播客扶持计划这是场耗资一个亿的复盘！ 我们将通过李潇这笔天价学费，拆解品牌创业的底层逻辑，拆解瑞幸古茗能够突出重围的核心方法论 #这很容易 #咖啡</a>
<a node_id=39 href="/video/7694599103497473307">共创1.8万上5天班，月入5万！用AI做自媒体博主这么爽？ #这很容易 #视频播客扶持计划 #抖音创作者大会 #抖音里的人 #AI新星计划上5天班，月入5万！用AI做自媒体博主这么爽？ #这很容易 #视频播客扶持计划 #抖音创作者大会 #抖音里的人 #AI新星计划</a>
<a node_id=40 href="/video/7688948653574212904">2125锐评网络修猫修狗视频 #宠物 #麦富迪猫粮 #麦富迪天性喂养 #麦富迪锐评网络修猫修狗视频 #宠物 #麦富迪猫粮 #麦富迪天性喂养 #麦富迪</a>
<a node_id=41 href="/video/7678555174301879593">1.6万碰到我的 AI ，你的钱包算是倒霉了... #这很容易 #抖来聊聊 #AI新星计划 #AI创业 #抖音前沿科技首发计划碰到我的 AI ，你的钱包算是倒霉了... #这很容易 #抖来聊聊 #AI新星计划 #AI创业 #抖音前沿科技首发计划</a>
<a node_id=42 href="/video/7676175824038481198">共创3.2万天才交易员拆解宇树，上市一天腰斩，4500 亿到底多少水分？ #宇树科技上市 #具身智能 #这很容易 #ai新星计划 #宇树科技天才交易员拆解宇树，上市一天腰斩，4500 亿到底多少水分？ #宇树科技上市 #具身智能 #这很容易 #ai新星计划 #宇树科技</a>
<a node_id=43 href="/video/7675594493516582144">11.3万娃衣经济：现在的 05 后真是有点让人害怕了.... #视频播客扶持计划 #这很容易 #抖来聊聊 #1688 #大学生创业娃衣经济：现在的 05 后真是有点让人害怕了.... #视频播客扶持计划 #这很容易 #抖来聊聊 #1688 #大学生创业</a>
<a node_id=44 href="/video/7671973160639532314">6.4万北大医学生被治成瘫痪后...我靠按摩重启人生！ #视频播客扶持计划 #抖来聊聊 #这很容易 #推拿 #北大北大医学生被治成瘫痪后...我靠按摩重启人生！ #视频播客扶持计划 #抖来聊聊 #这很容易 #推拿 #北大</a>
<a node_id=45 href="/video/7667162022697553190">共创7.6万为了当单身妈妈，女总裁付出了… #视频播客扶持计划 #这很容易 #AI新星计划 #抖来聊聊 #单亲妈妈创业为了当单身妈妈，女总裁付出了… #视频播客扶持计划 #这很容易 #AI新星计划 #抖来聊聊 #单亲妈妈创业</a>
<a node_id=99 href="https://www.douyin.com/video/7600000000000000001">999绝对路径链接也应被解析 #测试绝对路径#测试绝对路径</a>
"""


def test_extract_count_and_unique():
    items = watch.extract_works_from_dom(DOM)
    assert len(items) == 13, items
    ids = [i[0] for i in items]
    assert len(set(ids)) == 13, "id 不应重复"


def test_extract_views_shapes():
    items = dict((i[0], i) for i in watch.extract_works_from_dom(DOM))
    assert items["7682633651724569891"][2] == "置顶42.0万"
    assert items["7699082931108646159"][2] == "共创1.5万"
    assert items["7705023283414027561"][2] == "1937"        # 纯数字
    assert items["7688948653574212904"][2] == "2125"        # 纯数字
    assert items["7678555174301879593"][2] == "1.6万"
    assert items["7667162022697553190"][2] == "共创7.6万"


def test_extract_title_deduped():
    items = dict((i[0], i) for i in watch.extract_works_from_dom(DOM))
    # 标题取第一个 # 之前，且不带重复的第二遍
    t = items["7682633651724569891"][1]
    assert t == "抄底拼多多，A轮入宁德，24 岁带头举牌上市公司 宇宙E-BIKE 创始人程泓宁的逆向投资之路", t
    assert "#" not in t, "标题不应含话题标记"
    assert "AI新星计划" not in t, "标题不应含重复的第二遍话题"
    # @ 与 ｜ 应保留
    t2 = items["7705023283414027561"][1]
    assert t2.startswith("月薪四千到经营十亿，女总裁归零后如何再起？ @奔跑吧dita｜双11真破价到底"), t2


def test_extract_absolute_url():
    items = dict((i[0], i) for i in watch.extract_works_from_dom(DOM))
    assert items["7600000000000000001"][0] == "7600000000000000001"
    assert items["7600000000000000001"][1] == "绝对路径链接也应被解析"
    assert items["7600000000000000001"][2] == "999"


def test_scan_dom_end_to_end(tmp_path):
    watch.WATCH_ROOT = tmp_path
    watch.ACCOUNTS = tmp_path / "accounts.json"
    watch.SEEN = tmp_path / "seen.json"
    watch.QUEUE = tmp_path / "queue.json"

    dom = tmp_path / "dom.txt"
    dom.write_text(DOM, encoding="utf-8")

    sec = "MS4wLjABAAAAQUFn-KODXUGHd6PGbfJEmmzCIu3C1vzQBPgRprrkpfBMwWAXm09Vf48yEpik6k2W"
    args = types.SimpleNamespace(account=sec, dom_file=str(dom), nickname="这很容易")
    assert watch.cmd_scan_dom(args) == 0

    q = json.loads(watch.QUEUE.read_text(encoding="utf-8"))
    assert len(q["items"]) == 13, q["items"]
    first = q["items"][0]
    assert first["aweme_id"] == "7682633651724569891"
    assert first["title_hint"].startswith("抄底拼多多")
    assert first["views_hint"] == "置顶42.0万"
    assert first["account"] == sec


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
