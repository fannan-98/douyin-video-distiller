#!/usr/bin/env python3
"""把 search_discover.py 产出的搜索 JSON 转成一份可读的 HTML 报告。

搜索 JSON 里每条是 {video_id, title(=视频文案), author_nickname, web_url,
create_time, likes, comments}。它本身是给机器用的；这份脚本把它变成人能直接
在预览面板里"看"的卡片报告：按点赞排序、带可点击抖音链接、附主题归纳。

用法：
    python make_search_report.py --json search-赚钱方法-20261108.json \
        --out reports/search-赚钱方法-20261108.html
"""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


def clean_title(raw: str) -> tuple[str, list[str]]:
    """拆出正文与话题标签。抖音文案形如 '正文 #标签1 #标签2'。"""
    tags = re.findall(r"#\S+", raw or "")
    text = re.sub(r"#\S+", "", raw or "").strip(" \t\n-—")
    return text, tags


def fmt_num(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "-"
    if n >= 10000:
        return f"{n / 10000:.1f}w"
    return str(n)


def fmt_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "-"


def build_report(items: list[dict], keyword: str, generated_at: str) -> str:
    # 排序：点赞降序
    items = sorted(items, key=lambda v: int(v.get("likes") or 0), reverse=True)

    # 主题归纳：聚合所有话题标签
    tag_counter: Counter = Counter()
    for v in items:
        _, tags = clean_title(v.get("title", ""))
        for t in tags:
            tag_counter[t] += 1
    top_tags = tag_counter.most_common(12)

    cards = []
    for i, v in enumerate(items, 1):
        text, tags = clean_title(v.get("title", ""))
        title_html = html.escape(text) if text else html.escape(v.get("title", "(无文案)"))
        tag_html = " ".join(
            f'<span class="tag">{html.escape(t)}</span>' for t in tags[:8]
        )
        author = html.escape(v.get("author_nickname", "未知作者"))
        url = html.escape(v.get("web_url", "#"))
        date = fmt_date(v.get("create_time"))
        likes = fmt_num(v.get("likes"))
        comments = fmt_num(v.get("comments"))
        cards.append(f"""
        <article class="card">
          <div class="rank">#{i}</div>
          <div class="body">
            <h3><a href="{url}" target="_blank" rel="noopener">{title_html}</a></h3>
            <div class="meta">
              <span>👤 {author}</span>
              <span>📅 {date}</span>
              <span>👍 {likes}</span>
              <span>💬 {comments}</span>
            </div>
            <div class="tags">{tag_html}</div>
          </div>
        </article>""")

    tag_summary = "、".join(
        f"{html.escape(t)}({c})" for t, c in top_tags
    ) or "（文案中无话题标签）"

    total_likes = sum(int(v.get("likes") or 0) for v in items)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全站搜索发现 · {html.escape(keyword)}</title>
<style>
  :root {{ --bg:#f6f7f9; --card:#fff; --ink:#1f2329; --sub:#6b7280;
          --accent:#fe2c55; --line:#e5e7eb; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         line-height:1.6; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:28px 18px 60px; }}
  header h1 {{ font-size:24px; margin:0 0 4px; }}
  header .sub {{ color:var(--sub); font-size:14px; }}
  .stats {{ display:flex; gap:18px; flex-wrap:wrap; margin:16px 0 8px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:10px 14px; min-width:96px; }}
  .stat b {{ display:block; font-size:20px; color:var(--accent); }}
  .stat span {{ font-size:12px; color:var(--sub); }}
  .theme {{ background:#fff7f9; border:1px solid #ffd7e0; border-radius:10px;
            padding:12px 14px; margin:14px 0 22px; font-size:14px; }}
  .theme b {{ color:var(--accent); }}
  .card {{ display:flex; gap:14px; background:var(--card); border:1px solid var(--line);
          border-radius:12px; padding:14px 16px; margin-bottom:12px;
          transition:box-shadow .15s; }}
  .card:hover {{ box-shadow:0 4px 16px rgba(0,0,0,.06); }}
  .rank {{ font-size:18px; font-weight:700; color:var(--sub); min-width:30px; }}
  .body {{ flex:1; min-width:0; }}
  .body h3 {{ margin:0 0 6px; font-size:16px; line-height:1.5; }}
  .body h3 a {{ color:var(--ink); text-decoration:none; }}
  .body h3 a:hover {{ color:var(--accent); }}
  .meta {{ display:flex; gap:14px; flex-wrap:wrap; color:var(--sub);
           font-size:13px; margin-bottom:6px; }}
  .tags {{ font-size:12px; color:#2563eb; }}
  .tag {{ display:inline-block; margin-right:6px; }}
  footer {{ color:var(--sub); font-size:12px; margin-top:24px; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🔍 全站搜索发现 · {html.escape(keyword)}</h1>
    <div class="sub">近 30 天 · 拦截抖音官方搜索 XHR 所得 · 生成于 {html.escape(generated_at)}</div>
  </header>

  <div class="stats">
    <div class="stat"><b>{len(items)}</b><span>条视频</span></div>
    <div class="stat"><b>{fmt_num(total_likes)}</b><span>总点赞</span></div>
    <div class="stat"><b>{len(top_tags)}</b><span>个高频话题</span></div>
  </div>

  <div class="theme">
    <b>主题归纳（按文案话题标签聚合）：</b><br>{tag_summary}
  </div>

  {"".join(cards)}

  <footer>
    数据来自抖音搜索接口拦截，标题即视频文案。想看"视频里实际讲的方法"，
    可对任一视频跑蒸馏流程（下载+转写）拿到纯文字版方法拆解。
  </footer>
</div>
</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="把搜索 JSON 转成可读 HTML 报告")
    ap.add_argument("--json", required=True, help="search_discover.py 产出的 JSON")
    ap.add_argument("--out", default="", help="HTML 输出路径（默认同目录 .html）")
    ap.add_argument("--keyword", default="", help="报告标题用的关键词（默认从文件名推断）")
    args = ap.parse_args()

    src = Path(args.json)
    items = json.loads(src.read_text(encoding="utf-8"))
    # 兼容 [] 数组 或 {"videos":[...]} 两种形态
    if isinstance(items, dict):
        items = items.get("videos") or items.get("items") or []

    keyword = args.keyword
    if not keyword:
        m = re.search(r"search-(.+?)-(\d{8})", src.name)
        keyword = m.group(1) if m else src.stem

    out = Path(args.out) if args.out else src.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_report(items, keyword, datetime.now().strftime("%Y-%m-%d %H:%M")),
        encoding="utf-8",
    )
    print(f"报告已写出: {out}（{len(items)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
