---
name: douyin-video-distiller
title: 抖音视频精选 · 下载蒸馏入库
summary: 把一条抖音视频变成一份带出处、可追溯的结构化蒸馏笔记，并保存到 ima 独立知识库「抖音视频精选」（YOUR_IMA_KB_ID）。
description: 当用户贴来抖音视频链接（含 modal_id 搜索链接、douyin.com/video/...、分享短链）并要求「下载、蒸馏、拆解、整理、保存到知识库、放进抖音视频精选」时使用；也用于用户已把**单条**抖音视频保存到本地、或已有一条视频的字幕/文稿，希望沉淀成**一篇**知识库笔记的场景（整门课、多讲字幕、要按讲次拆成一组 skill 的，走 `doudian-course-distiller`，不走本 skill）。端到端覆盖：解析 video_id → 取得真实文字（字幕 / 本地文件转写 / 下载后转写）→ 抓页面元信息并自动抽取抖音官方章节要点 → 按证据分级蒸馏 → 上传 ima 独立知识库「抖音视频精选」并验证。质量红线：拿不到真实文字来源时必须停下并输出素材卡，禁止凭标题、话题或记忆臆造内容，禁止把推荐流或同作者其他视频当作本条视频的观点；禁止只查 chapter_list 一个键就判定「章节要点未取得」。触发词：下载抖音视频、蒸馏这条视频、下载并蒸馏、保存到抖音视频精选、放进知识库、这条抖音整理一下、章节要点、分享短链、跑抖音精选。v1.5 支持整段分享口令原文、短链展开与转写被拒时的可审计降级。
version: 1.17
agent_created: true
scope: 抖音视频内容沉淀 → ima 独立知识库「抖音视频精选」(YOUR_IMA_KB_ID)
read_when:
  - 用户贴抖音链接并说下载 / 蒸馏 / 整理 / 存知识库
  - 用户给本地抖音视频文件、字幕或文稿，要求沉淀进知识库
  - 用户说「放进抖音视频精选」「保存到那个知识库」
---

# 抖音视频精选 · 下载蒸馏入库

## 一、目标与边界

把**一条**抖音视频变成**一份**带出处、可追溯的结构化笔记，落到 ima 独立知识库「抖音视频精选」（`YOUR_IMA_KB_ID`）根目录，并验证入库成功。

范围内：解析链接、取得逐字稿、抓元信息、蒸馏、上传、验证、断点续跑。
范围外：批量爬取、绕过付费或登录墙、下载他人受版权保护内容用于再发布、把视频观点当成事实背书。笔记中保留来源链接与作者，引用保持简短并标注出处。

**与相邻 skill 的分界（2026-11-08 补，此前只写在 `references` 深处，加载主文件时看不到）**：

| 输入 | 去处 |
| --- | --- |
| **一条**抖音视频（链接 / 单个本地文件）→ 要**一份**可查证笔记 | 本 skill |
| **一门课 / 多讲字幕 / 按讲次拆成一组原子 skill** | `doudian-course-distiller` |
| 商品卡、自然流、选品定类目的**运营方法本身** | `doudian-natural-flow-starter` |

判别只看输入粒度：**一条 → 笔记**，**一门 → 一组 skill**。本 skill 只负责把视频内容蒸成可查证的笔记，不替代运营判断；用户没说的类目、平台、经营重点一律不写入笔记（见 `assets/note-template.md` 第七节）。

## 二、固定配置（2026-11-08 已核实）

| 项 | 值 |
| --- | --- |
| 目标知识库 | 「抖音视频精选」`YOUR_IMA_KB_ID`（用户新建的独立知识库，可写，当前 0 条） |
| 目标位置 | 该知识库**根目录**；它本身就是专用库，无需再找子文件夹，`add_knowledge` 不传 `folder_id` |
| 备用知识库 | 「电商」`YOUR_IMA_KB_FALLBACK_ID`，仅当用户明确指定时使用 |
| 工作目录 | `D:\douyin-distill\<video_id>\`（C 盘长期吃紧，产物一律放 D 盘） |
| 笔记命名 | `【WorkBuddy-YYYY-MM-DD】<video_id>-<slug>.md`，沿用知识库内既有命名习惯 |
| 上传格式 | Markdown，`content_type=text/markdown`，`file_ext=md` |
| 章节要点字段 | `aweme_detail.recommend_chapter_info.recommend_chapter_list`（**不是** `chapter_list`） |
| 浏览器 | 一律 Edge，不用 Chrome 或默认浏览器 |

> 注意：「抖音视频精选」是**独立知识库**，不是「电商」里的文件夹。2026-11-08 核实：个人知识库列表、可添加知识库列表、知识库搜索均能查到该 ID；「电商」里没有同名文件夹。

## 二·补、默认调用约定（2026-11-08 用户确认）

这个 Skill 会被高频调用，为减少每次重复交代，约定如下：

- **「下载并蒸馏」= 走完整链路并把正式蒸馏笔记存入 ima「抖音视频精选」**，不是只把视频文件下到本地。用户此前明确说过「下载并蒸馏理解为蒸馏笔记存进去」。
- **只贴一条抖音链接、或说「跑抖音精选 <链接> / 蒸馏这条 / 整理一下 / 放进抖音视频精选」即视为触发**；`modal_id` 搜索链接、`/video/<id>`、分享短链、纯数字 id 都能识别，不需要用户先转成规范 URL。
- **下载只是取得真实文字来源的手段**，交付物是笔记入库。只有用户明确说「只要视频文件 / 不要入库 / 先别存」时，才停在阶段 1C 或阶段 3，不调用 `create_media` / `add_knowledge`。
- **以下固定项已写死，不要每次重新向用户确认**：知识库 ID `YOUR_IMA_KB_ID`、写根目录不传 `folder_id`、同名用 `DUPLICATE_NAME_STRATEGY_REPLACE`、工作目录 `D:\douyin-distill\<video_id>\`、浏览器 Edge、章节字段 `recommend_chapter_info.recommend_chapter_list`。
- **例外**：拿不到真实文字来源时按红线输出素材卡并停下，绝不生成正式蒸馏笔记；素材卡只在用户要求时才按阶段 4 作为显式标注「未完成」的占位入库。
- **位置**：Skill 本体固定放在用户级 `~/.workbuddy/skills/douyin-video-distiller/`（跨所有项目生效）。不要移到项目级 `{workspace}/.workbuddy/skills/`：工作区目录带时间戳，换会话就失效。产物与备份放 D 盘，不放进 Skill 目录。
- **抖音分享口令可以整段原样粘贴**（含「9.74 复制打开抖音…」「Jvf:/」等噪声）。阶段 0 会自己抽出 URL、展开短链、逐跳校验域名，不需要用户手工转成 `/video/<id>`。
- **v1.7 起优先用 `scripts/run_pipeline.py` 的 `prepare → check → finalize` 三命令**，不要再手工拼阶段命令；`prepare --use-local-asr` 会调用装在本机 D 盘的离线 ASR，云端转写工具被拒也不影响交付逐字稿型笔记。换机或后端异常时先跑 `scripts/setup_local_asr.py --check`，一条命令完成幂等部署（v1.8）。详见阶段 0。
- **离线 ASR 装在本机 `D:\WorkBuddy-ASR\`**（sherpa-onnx 2.0.1 + SenseVoice int8 + silero VAD，模型 239 MB）；不占 C 盘、不自动下载、不需要联网。首次部署与命令见 `references/douyin-download.md §12`。

## 三、工作流决策树

```
输入（整段分享口令 / 短链 / 链接 / 字幕 / 本地视频）
├─ 阶段 0 resolve_input 展开短链 .................→ 拿到 video_id
├─ 已有字幕或文稿 ................................→ 阶段 2 → 门禁 → 阶段 3 → 阶段 4
├─ 已有本地视频文件（App 保存 / 录屏）...........→ 阶段 1B 转写 → 阶段 2 → 门禁 → 阶段 3 → 阶段 4
└─ 只有链接
   └─ 阶段 1C 签名直连 → 阶段 2（章节 + 字幕探测）
        ├─ 阶段 1B' 本地离线 ASR（v1.7，prepare --use-local-asr）
        │     ffmpeg 16k → silero VAD 分段 → SenseVoice → source/transcript.md
        └─ 阶段 1E 门禁 transcription_gate
             ├─ full_note（逐字稿 / 官方字幕）......→ 正式笔记·逐字稿型 → lint → 阶段 4
             ├─ limited_note（章节 + 文案）.........→ 正式笔记·章节型（如实标来源）→ lint → 阶段 4
             ├─ source_card（只有元信息）...........→ 素材卡并停止
             ├─ blocked（policy=fail）..............→ 停止，等用户明确指示
             └─ transcription_pending（已授权）.....→ 调转写工具，成功后再跑门禁 --state done
```

> `full_note` / `limited_note` / `source_card` 是 `transcription_gate.py` 的**机器字段名**，不是交付物等级标签。前两者都产出完整正式笔记，只是证据来源不同；对外一律说「正式笔记·逐字稿型 / 章节型」。

> **章节要点不再靠浏览器点面板。** 只要拿到 `api/detail.json`，阶段 2 的 `extract_chapters.py` 就能拿到页面「章节要点：共 N 个」的 N 条原文与官方总摘要。2026-11-08 实测：`chapter_list=null`、`anchors=null`、`video_text=[]`，但 `recommend_chapter_info.recommend_chapter_list` 有 5 条完整章节。任何时候写「章节要点未取得」，都必须附上脚本输出的 `checked_paths` 审计清单。

## 三·补、无人值守采集（v1.9）

除了「用户贴一条链接」，本 Skill 还支持**每天凌晨自动发现监控账号的新视频并批量采集转录**（对应「凌晨自动采集、早上给判断」的需求）。三个脚本 + 两条自动化：

| 环节 | 谁做 | 命令 / 工具 |
| --- | --- | --- |
| 发现新作品 | **agent + Edge 浏览器**（HTTP 路线全被封，见 `references/watch-feeds.md`） | `browsers_list` → 打开 `douyin.com/user/<sec_uid>`（**必须是 `family=edge` 的登录态实例**）→ `dom_cua_get_visible_dom` → 存成文件 → `watch.py scan-dom --account <sec_uid> --dom-file <文件>`（一步完成发现 + 去重 + 入队） |
| 去重入队（备用） | 脚本 | 已含在 `scan-dom` 内；`watch.py scan-result --account <sec_uid> --items "id~标题~播放量\|…"` 仅在你手头已有 id 列表而非整页 DOM 时用 |
| 修台账 | 脚本 | `watch.py reconcile`（回收卡死 / 按产物推状态 / 重试可重试项） |
| 下载 + 转录 | 脚本 | `run_batch.py --limit 8 --budget-minutes 90`（默认新内容优先） |
| 出晨报底稿 | 脚本 | `daily_brief.py` → `_watch/brief/<日期>-<HHMM>.md` 与 `latest.md` |
| 全站搜索发现（关键词维度） | 脚本 + 独立 Chromium | `search_discover.py --keyword "赚钱方法" --since-days 30`（首次需 `login_auto.py` 扫码，详见 §三·补 关键词搜索；遇滑块加 `--human-wait`） |
| 取舍 + 写笔记 + 入库 | **agent**（早间） | 按阶段 3/4 正常流程走 |

**状态机（v1.10 起，务必分清）**：

```
queued → processing → collected → distilled → published
                          ↑            ↑            ↑
                    凌晨任务到此为止   lint 通过    入库并 finalize 验收
```

`collected` 只代表「逐字稿就绪、还欠笔记」。v1.9 把它和「已入库」混成一个 `done`，一次真实批量后 4 条 `done` 里有 3 条根本没有笔记——台账替不存在的成果作了证。`watch.py status` 的 `needs_distillation` 就是当前欠几篇。

**`published` 不是不可回退的终态（v1.11）**：入库之后笔记若被改写，ima 里就变成一份旧版本，台账不能继续替它作证。判定比的是**内容哈希**（`note.sha256`），不是字节数——只比大小等价于 Nextflow 的 `cache 'lenient'`，其文档明写「can miss same-size content changes」。指纹不符时状态降回 `distilled`，理由写成 `⚠ 本地笔记已被改写：sha256 A ≠ 入库版本 B，需重新 finalize`，并计入晨报的「笔记已漂移」计数。遇到这条不要改状态蒙混过去，正确动作是重跑 `check → 上传 → finalize --receipt`。

**为什么发现层必须用浏览器**：`/aweme/v1/web/aweme/post/` 即使带完整 a_bogus + x-secsdk 签名也返回 HTTP 200 + **0 字节**（静默风控）；`iesdouyin` 老接口返回 `status_code=11110`；用户主页 HTML 是 6.4 KB 验证页。而 `/aweme/detail/` 对游客仍开放——所以**浏览器只负责拿 ID，元信息/章节/下载继续走详情接口**。实测数据见 `references/watch-feeds.md`。

**节奏**：02:30 采集转录（纯确定性），09:30 出晨报并判断取舍（要模型判断）。合并也行，但先出清单再蒸馏能避免白干。

**降级**：浏览器不可用时不要试 HTTP 接口，改用 `watch.py inbox`（`_watch/inbox.txt` 一行一个链接）手工投递，并在报告里写明降级原因。

### 关键词搜索：全站发现通道（v1.16 新增，可行）

**游客态确实无搜索**（上文六条路全死，结论不变）。但「登录态 + 页面内 XHR 拦截」这条路走得通——前提是登录身份来自**工具自己的独立浏览器**，不是你的 Edge cookie。

GitHub 上大量标「免登录/不封号」的项目其实是付费 token 马甲（guaikei 类，已删），逆向爬虫要抠 Edge 的 v20 加密 cookie（本机解不开、且违反红线 #15）。唯一可免费落地的开源方案是 **`zhubaohe123/Douyin-api`（MIT）**，机制是 Playwright 启动**独立 Chromium**，导航到搜索页后 `page.on("response")` 拦截官方 `/aweme/v1/web/general/search/single/` XHR——拦截的是页面内请求，带着登录上下文，所以绕过了 `blocked`。已 vendored 到 `D:\douyin-distill\_vendor\douyin-api`。

**合规要点（对照红线 #15）**：该工具只用**自己扫码登录出来的全新 profile**，把 session 存成 `cookies.txt`（在 vendor 目录内）；**绝不读取、复制或解密你的 Edge cookie / profile**。它和「去读用户浏览器登录态」是两条完全不同的路。

**首次（一次性）**：在 vendor 目录用其 venv 跑 `login_auto.py`，弹出可见浏览器扫码登录；脚本每 3 秒自动检测登录态（出现 `sessionid`/`sid_guard`/`sid_tt` 等即存盘 `cookies.txt`），无需手动回车，也无需先搜词激活。cookie 过期后重跑即可。

**之后每天**：`search_discover.py --keyword "赚钱方法" --since-days 30`（默认 `publish_time=180&sort_type=2`，本地按 `create_time` 过滤近 30 天）→ 产出 `search-<词>-<日期>.json` → 自动调用 `watch.py scan-search --json` 入队去重。多作者、无单一账号，故队列按各视频作者 `sec_uid` 落账，标记 `discovered_via=search`。

> **踩坑（v1.17 实测）**：抖音风控对「刚登录就猛搜」极其敏感。**每次搜索前务必先预热**（已内置：访问首页 + 轻量滚动，生成 `ttwid`/`msToken`）。**绝对不要连刷**——一次跑 3 连发重试会把风险分打满，从「JSON 里返回 `verify_check`」升级成「页面弹出滑块验证码」，之后整段时间都被卡。正确做法：**单发 + 预热即可过**；若仍弹出滑块，用 `--human-wait 240` 让脚本保持浏览器打开、最多等 240 秒，**你手动在弹出窗口里拖完滑块**，脚本会自动接管拿到结果（免费、不接付费打码）。实测单次预热搜索稳定返回约 9 条近 30 天视频。

> 完整「全站近 30 天赚钱方法」= 三路合并：① 本搜索通道（关键词维度发现）② 账号维度 `scan-dom`（既有）③ 源视频第 1/3/6 条方法（谁在靠什么赚钱 → 四步调研链路 → 凌晨采集早上判断）。搜索结果只负责「发现」，蒸馏/入库仍走阶段 3/4 正常流程。

**单夜上限 8 条**、账号间 ≥15 秒、条目间 20 秒（沿用账号扫描节奏）。实测一条 16 分 43 秒视频下载 + 62 段转写共 95 秒；8 条约 13 分钟。

## 四、阶段 0 — 解析输入与建工作目录

**v1.7 推荐路径：三条命令走完整链路**（`run_pipeline.py` 自动建目录、自动选 `run<N>`、自动处理「无字幕」这类非错误退出码；`R` 从 `prepare` 输出的 `run_dir` 取）：

```bash
P="YOUR_SKILL_DIR/scripts/run_pipeline.py"
RAW="<整段分享口令原样粘贴，不要手工转 URL>"

# 1. 阶段 0→1C→1B'→2→1E，输出 handoff.json（写笔记所需的全部素材路径）
python "$P" prepare "$RAW" --use-local-asr

# 2. 按 handoff.json 写好笔记后，跑 lint 硬门槛并生成 upload-manifest.json
python "$P" check --run-dir "<run_dir>"

# 3. 用 manifest 的参数调 MCP 完成 create_media → PUT → add_knowledge → get_knowledge_list，
#    把 get_knowledge_list 的真实返回存成 receipt.json，再交给 finalize 交叉校验
python "$P" finalize --run-dir "<run_dir>" --receipt "<run_dir>/ima-receipt-raw.json"
```

**`finalize --receipt` 是「入库已核对」的唯一凭据**（v1.8）。阶段 4 无法脚本化——ima 是远程 OAuth MCP（`https://ima.qq.com/mcp`），token 属于宿主进程而不属于脚本，硬造一个私有 token 通道既脆弱又越权。所以改成反向校验：把 `get_knowledge_list` 的真实返回交给 `finalize`，它逐项比对**文件名、字节数、`parse_progress`、PUT 状态码**，任何一项不符就拒绝写 `status=completed` 并打印 `RECEIPT MISMATCH`。这拦的正是最常见、肉眼看不出的错——上传了上一版草稿（体积对不上）。落盘留档 `ima-receipt.json`，机器可复查。

**回收旧试跑目录**（v1.8，语义取自 `nextflow clean`）：每次 run 都会存一份完整媒体（约 107 MiB/次），重跑几次就吃掉一个 G，这是以前 `run1` 那种半成品目录一直堆着没人管的真实原因。默认 dry-run，必须 `-f` 才动手：

```bash
python "$P" clean                 # 只报告，不动文件
python "$P" clean -f              # 每视频保留最近 2 次；失败目录整删，已完成目录只清媒体
python "$P" clean --keep-last 1 --video-id <id> -f
```

与 nextflow 有意不同的一点：**已完成的 run 永远不整目录删除，只清 `api/` 里的音视频**。笔记已在 ima 里，但 `status.json` / `STATUS.md` / `lint-note.json` 是「这条到底发了什么」的唯一本地审计记录，删了就无从复查；而失败 / 废弃的 run 没有这种价值，直接整删。`media` 清掉后该 run 不能再离线重跑，但审计链完整。

`prepare` 不加 `--use-local-asr` 时，门禁如实记 `state=skipped`（不谎报「工具被拒」），按章节型笔记正常交付；`--no-download` 可复用已有 `api/detail.json` 做离线排查。逐条命令与退出码都写在 `pipeline-steps.json`。

<details><summary>手动模式（排查单步时用）</summary>

```bash
S="YOUR_SKILL_DIR/scripts"
R="D:/douyin-distill/<video_id>/run<N>"
mkdir -p "$R/api" "$R/source"     # 必须先建目录，早期踩过 tee 先于 mkdir 的坑
python "$S/douyin_fetch.py" "$RAW" --workdir "$R" --normalize-only --json-out "$R/stage0.json"
python "$S/douyin_detail.py" <video_id> --download --out "$R/api" --workdir "$R"
python "$S/local_asr.py" --workdir "$R"                     # 可选，本地离线转写
python "$S/probe_subtitles.py" --workdir "$R"               # 无字幕时退出码 3 属正常
python "$S/extract_chapters.py" --workdir "$R"              # 无章节时退出码 3 属正常
python "$S/transcription_gate.py" --workdir "$R" --state done --reason "<如实原因>"
python "$S/lint_note.py" --note "$R/<笔记>.md" --workdir "$R" --json-out "$R/lint-note.json"
```

</details>

1. 从输入中提取 `video_id`：**由 `resolve_input.py` 完成**（分享口令 → 抽 URL → 短链逐跳展开 → 从视频路径取 id），不要手写 `\d{15,25}` 正则去猜。兼容 `modal_id=`、`/video/<id>`、`share/video/<id>`、短链与纯数字。
2. 生成规范 URL `https://www.douyin.com/video/<video_id>`。
3. 创建 `D:\douyin-distill\<video_id>\`，写入 `STATUS.md` 记录当前阶段、文字来源、产物路径。**机器可读状态只有一个 `<workdir>/status.json`**，由阶段 0 创建、后续各阶段用 `dy_http.merge_status` 合并写入；禁止再写 `api/status.json` 之类的第二份状态文件，否则断点续跑会读到互相冲突的 `status`。中断后从该文件恢复。
4. 使用 `scripts/douyin_fetch.py "<输入>" --workdir "<工作目录>" --normalize-only` 做输入归一化与状态初始化。**必须带 `--normalize-only`**：不带它会去跑旧的 `yt-dlp` 路径，并写出「不要蒸馏、不要入库」的误导性 `status.json`。归一化只解析 video_id、建目录、写 `status.json`，下载交给阶段 1C。
   **v1.5 起 `douyin_fetch.py` 自动调用 `scripts/resolve_input.py`**：从整段分享口令里抽第一个 URL、按 host 白名单展开 `v.douyin.com` → `iesdouyin.com/share/video/<id>` → `douyin.com/video/<id>`，最多 5 跳、逐跳重新校验域名，从**视频路径**取 id（不能用「最长数字」，分享链里的 21 位 `social_author_id` 不是视频 id）。`status.json` 会写入 `input_resolution` 审计（input_type / hops / chain / failure_class）。也可单独跑 `python scripts/resolve_input.py "<原文>"` 排查。

## 五、阶段 1 — 取得真实文字（红线所在）

按优先级选择，**任何一步拿到逐字稿即可进入阶段 2**：

- **1A 字幕 / 文稿 / 复用已核实逐字稿**：用户粘贴，或 `.srt / .vtt / .txt / .md`，存为 `source/transcript.md`。最可靠，优先建议用户走这条。**当 1B 的转写调用被拒绝、失败或不值得重复时**，执行 `python scripts/resolve_transcript.py --workdir "<workdir>" --source "<上一次运行目录>"`：它会拒绝素材卡、强制校验当前 mp4 与母版 mp4 的 SHA256 一致，通过后带溯源说明写入 `source/transcript.md` 并更新 `status.json`。校验不通过一律停止，不允许凭记忆或标题补写。
- **1B 云端转写工具**：调用音频转写工具，传入本地文件绝对路径。支持 mp4 等格式，上限 1 GB / 5 小时。转写结果存 `source/transcript.md`。**被工具审批拒绝时按红线 11 停，不静默重试**；可直接改用 1B'，不必回头问用户。
- **1B' 本地离线 ASR（v1.7，推荐）**：`python scripts/local_asr.py --workdir "<workdir>"`，自动从 `api/<id>.mp3` 或 `.mp4` 取音频 → ffmpeg 16 kHz 单声道 → silero VAD 分段 → sherpa-onnx SenseVoice int8 转写 → 写 `source/transcript.md`（`[m:ss.mmm,m:ss.mmm] 文本` 格式，门禁可识别为 `full_note`）与 `source/local-asr.json`。先用 `local_asr.py --probe` 查后端。20 分 22 秒音频实测 87 秒转完、77 段语音 / 71 段有效文字、22,657 bytes；产品名听写有误差，笔记里以作者文案 / 官方章节为准，逐字稿写法只作留痕。
- **1C 在线下载（v1.3 首选）**：执行 `scripts/douyin_detail.py <video_id> --download --out "<workdir>/api" --workdir "<workdir>"`。用内置纯 Python 签名包（a_bogus + x-secsdk-web-signature）直连 `/aweme/v1/web/aweme/detail/`，再按 `video.play_addr.url_list` 下载 mp4。**不要再以 `yt-dlp` 作为默认路径**——它在本机稳定报 `Fresh cookies needed`。
  v1.3 的健壮性（借鉴 GitHub 成熟实现）：跟随 3xx 重定向（实测候选 URL 里确有 302）、网络错误/5xx 指数退避重试、`ftyp`/`ID3` 魔数与 Content-Length 校验后才落盘、已存在合法 mp4 时幂等跳过（`--force` 可覆盖）、失败按 `signature_refused / risk_control / empty_payload / network_*` 分类并指向要更新的签名常量、访客 cookie 默认**不落盘**（`--save-session` 才写且限权）。`status.json` 的 `failure_class` + `hint` 是排错第一入口。
  2026-11-08 实测：HTTP 200 / `status_code:0`，下载 108 MB 完整视频。命令与原理见 `references/douyin-download.md`。
- **1D 章节要点（文字来源之一，v1.4 改为从接口取）**：优先由阶段 2 的 `scripts/extract_chapters.py` 从 `api/detail.json` 抽取，不依赖播放器能否加载、不需要点开面板。未使用口播逐字稿时，按阶段 3 写**正常的章节型精选笔记**，在「来源与证据」如实写明证据来源即可；**不得**在顶部标「有限模式 / 缺口 / 未完成」，也不得因此停下要用户确认。浏览器 DOM 仅作交叉核对，不再是唯一来源。

- **1E 转写门禁（v1.5 必跑，不可跳过）**：无论 1A/1B 成功与否，进入阶段 3 之前必须执行
  ```bash
  python scripts/probe_subtitles.py --workdir "<workdir>"
  python scripts/transcription_gate.py --workdir "<workdir>" --state <done|declined|failed|granted|skipped> --reason "<如实原因>"
  ```
  `probe_subtitles.py` 会遍历 `detail.json`（含 JSON 字符串二次解码）确认有没有官方字幕轨，输出 `checked_paths`；`desc` / `caption` 是作者文案，明确标 `is_transcript=false`，绝不冒充逐字稿。`transcription_gate.py` 借鉴上游 `transcriptions_service.py` 的终态与 `fallback/fail` 策略，**覆盖写入**顶层 `status` 与 `next_step`，并给出唯一允许的交付等级 `full_note / limited_note / source_card / blocked / transcription_pending`。
  `no_silent_retry=true` 时（工具被拒或失败）禁止再次调用同一工具；只有用户明确授权后才可用 `--state granted` 重新打开。工具成功后写入 `source/transcript.md`，再跑 `--state done` 升级为 `full_note`。`--policy fail` 用于「不许降级」的场景，任何文字缺失一律 `blocked`。
  门禁会探测本机本地 ASR 后端并写入 `local_backends`。v1.7 起它会额外调用 `local_asr.py --probe` 识别装在 D 盘、不在当前解释器 `sys.path` 里的 sherpa-onnx 安装（`sherpa_onnx_sensevoice_local.available=true`），`offline_option` 会直接给出可用命令。其余后端（faster-whisper / FunASR / whisper.cpp）仍只探测、不自动安装；部署说明见 `references/douyin-download.md §12`。

**红线**：以上全部失败时，套用 `assets/note-template.md` 的「素材卡」部分，固化已核实的页面原文、失败复盘与补全路径，然后**停止蒸馏**，向用户索取字幕或本地文件。禁止生成正式蒸馏笔记、禁止凭标题臆造观点。素材卡可按阶段 4 入库作为「未完成」占位记录，但标题与正文必须显式标注该状态，不得冒充蒸馏笔记；用户未要求入库时只落本地。

## 六、阶段 2 — 抓取页面元信息与章节要点

### 2.0 字幕探测（v1.5 新增）

```bash
python scripts/probe_subtitles.py --workdir "<workdir>"
```

产出 `source/subtitle-probe.json`。有官方字幕轨时写入 `source/subtitles.json`，可作为与逐字稿同级的真实文字来源；没有时明确 `ok=false` 并列出全部 `checked_paths`。**禁止凭「抖音应该有字幕」的猜测往下走**。2026-11-08 实测本条 Claude Code 视频：11 个候选路径全空，唯一命中的 `music.lyric_short_position` 是歌词位置、不是字幕。

### 2.1 必做：抽取章节要点

```bash
python scripts/extract_chapters.py --workdir "<workdir>"
```

脚本按优先级检查 `api/detail.json` 里的多个候选字段，产出 `source/chapters.md`（人类可读：官方总摘要、N 条章节表、逐条原文、字段检查清单）与 `source/chapters.json`（机器可读，含 `timestamp_unit` 与 `checked_paths`），并合并进唯一的 `status.json`。

**字段优先级（第一位才是真正在用的）**：

| 顺序 | 路径 | 说明 |
| --- | --- | --- |
| 1 | `aweme_detail.recommend_chapter_info.recommend_chapter_list` | **页面「章节要点：共 N 个」的真实数据源**，含 `desc` / `detail` / `timestamp`(ms) |
| 1b | `aweme_detail.recommend_chapter_info.chapter_abstract` | 官方总摘要 |
| 2+ | `chapter_list` / `chapter_info.chapter_list` / `anchors` / `video_text` / 顶层 `chapter_list` | 通常为 `null` / `[]`，仅作兼容 |

时间戳单位是**毫秒**（`2000 → 00:02`、`335000 → 05:35`）；脚本用 `video.duration` 自动判定单位。早期版本用「大于 10 万才算毫秒」的朴素规则，把前两章误写成 33:20 / 283:20，已修复。

**判定规则**：`chapters.json` 中 `ok=true` 且 `count>0` 时，笔记里必须写「共 N 个章节」并给出完整章节表，**不得再写「章节要点原文未取得」**。只有脚本返回 `ok=false`（退出码 3）并附上全部候选字段的 `checked_paths` 时，才允许在「未取得字段」里列出章节要点。

### 2.2 元信息

**优先读 `api/detail.json`**：`aweme_detail` 里直接有 `desc`、`author.nickname`、`create_time`、`video.duration`、`statistics`、`text_extra`、`recommend_chapter_info`，比浏览器 DOM 更完整且可复现。浏览器只用于补 DOM 独有字段（作者主页、作者声明）。

需要用浏览器时：使用 Edge 扩展浏览器渲染页面（先从 `browsers_list` 取 browser_id，扩展掉线时改用静态抓取兜底）。收集并原样记录：

- 标题 / 文案全文（含话题标签）、作者、作者主页、发布时间、点赞 / 评论 / 收藏 / 分享
- 作者声明、原声、规范 URL、抓取时间
- 「章节要点：共 N 个」的计数，用于与 `chapters.json` 的 `count` 交叉核对

推荐流里出现的其他视频、同作者其他作品**只作旁证**，单独列一节并注明「不可当本条视频观点引用」。未取得的字段一律写「未取得」，不得填空或推测；但「未取得」必须是在跑完上面的抽取脚本、核对过审计清单之后才能下结论。

## 七、阶段 3 — 蒸馏

0. **先读 `source/transcription-gate.json` 的 `deliverable`**：`full_note`（逐字稿型）与 `limited_note`（章节型）**都是正式精选笔记**，只是证据等级不同。后者以官方章节要点 + 总摘要 + 作者文案为依据正常成文、正常给可执行清单，在「来源与证据」如实写明证据来源即可，不使用 `【原文】`（口播逐字稿专属）。**禁止**用「未完成 / 缺口 / 不覆盖全部内容 / 有限笔记 / 缺字稿」自贬，也不得停在素材卡或回头问用户要不要继续。门禁未跑或状态不匹配时禁止动笔。
1. 复制 `assets/note-template.md` 为 `<video_id>-digest.md`，按 `references/distill-spec.md` 填充。
2. **「官方章节要点」独立成节**（模板第三节前）：写入 `chapter_abstract` 总摘要 + N 条章节表（时间点 / 标题 / 摘要原文），逐条标 `【平台摘要】`，并注明「抖音平台 AI 章节摘要，非口播逐字稿」。章节时间点与逐字稿段落对得上时，在节末写一句交叉核对说明；对不上时如实写差异，不强行调和。
3. 核心要点按 `【原文】`（仅逐字稿型）/ `【文案】` / `【归纳】` / `【推断】` 标注，并挂时间戳或原句锚点；`【平台摘要】` 只允许出现在第三节，不混进核心要点或可执行清单。四级定义见 `references/distill-spec.md §2`。
4. 原文引用单段不超过 150 字；ASR 听写不确定项保留原样并标 `[ASR不确定]`，不猜产品名或数字。
5. 「可迁移点」单独成节（旧称「与业务的关联」），统一标为 `【推断】`，与视频原意严格分开。**使用者的场景只能来自 `_watch/profile.md` 或他当次说的话**；profile 为空时写「本条不迁移：与当前场景无交集」，不要替他假定类目、平台或经营重点。
6. 同步保留 `source/transcript.md`（有则）、`source/meta.md`、`source/chapters.md`，保证可回溯；元信息表的「章节数」写实际条数与来源字段。章节表与「逐条原文」的时间点、标题必须逐条对齐 `chapters.json`，禁止从其他视频复制粘贴。
7. **写完必须跑 `python scripts/run_pipeline.py check --run-dir "<workdir>"`**（等价于直接跑 `lint_note.py`，但会额外生成 `upload-manifest.json`）：退出码非 0 时按 `errors` 改笔记，**禁止入库**。它拦截自贬措辞、`【平台摘要】` 越界、未定义证据标记、占位符残留、章节数 / 时间点不一致、章节型笔记误用 `【原文】`。

深度模式：仅当用户明确说「做成 skill / 拆成 skill / 生成能力卡」时启用，按 RIA-TV++ 走 `BOOK_OVERVIEW → candidates → verified → SKILL.md → INDEX/GLOSSARY → test-prompts.json → DIGEST`，并把通过验证的产物一并入库；否则默认只产出一份精选笔记。

## 八、阶段 4 — 保存到知识库

完整参数与调用序列见 `references/ima-upload.md`。概要：

0. **前置硬门槛**：阶段 3 的 `lint_note.py` 必须已跑且 `error_count=0`；`lint-note.json` 的 `gate` 不是 `BLOCK UPLOAD`。用 `run_pipeline.py check` 时直接从 `upload-manifest.json` 取 `file_name` / `file_size` / `content_type` / `knowledge_base_id`，不要手工量字节数。未通过就先改笔记，不要带着已知错误上传。
1. 目标为独立知识库「抖音视频精选」`YOUR_IMA_KB_ID`，**直接写该库根目录**，不需要 `folder_id`。
2. 可选校验：`get_knowledge_base_list` / `get_addable_knowledge_base_list` 确认该库存在且 `can_add_knowledge=true`；库不存在或不可写时停下报告，**不改投其他知识库**。
3. `create_media` 申请 media_id 与预签名上传信息（`apply_upload_mode=1`，`knowledge_base_id=YOUR_IMA_KB_ID`）。
4. 用 `scripts/upload_presigned.py` 执行 HTTP PUT，原样携带 `auth_headers`，`Content-Length` 与 `file_size` 完全一致。
5. `add_knowledge` 只带 `knowledge_base_id` + `media_id`（**不传 `folder_id`**），重名策略用 `DUPLICATE_NAME_STRATEGY_REPLACE`（重跑同一条视频时覆盖）或 `SAVE`（保留历史版本）。
6. 用 `get_knowledge_list(knowledge_base_id=YOUR_IMA_KB_ID)` 验证标题已出现，再更新 `STATUS.md`。

**写入工具缺失时**：本次会话曾出现 `create_media` / `add_knowledge` 不在工具索引里的情况。此时先调用 `workbuddy_request_mcp_connection`，`servers` 只填 `["ima-mcp"]`，连接恢复后重试原调用；不要改投 `LibraryUpload` 等近似工具，也不要声称「已入库」。2026-11-08 实测走该流程后工具恢复，PUT 200、`add_knowledge` 成功、`knowledge_total_size=1`。

## 九、阶段 5 — 回报

向用户报告：video_id、**交付类型（正式笔记·逐字稿型 / 正式笔记·章节型 / 素材卡 / 已停止）**、文字来源（本地离线转写 / 云端转写 / 复用 / 章节要点 + 总摘要 + 作者文案）、**章节要点条数与来源字段**、核心要点条数、lint 结果、入库文件标题与字节数、目标知识库、验证结果，以及未取得的元信息字段。未取得的字段如实列出，不掩盖；`chapters.json` 里 `count>0` 却写「章节要点未取得」视为交付事故。**禁止**把「没跑口播转写」说成「缺字稿 / 缺口」，或把已交付的章节型笔记说成残次品、半成品、有限笔记。

用 `run_pipeline.py` 时，验证过 `get_knowledge_list` 后把真实返回存成 JSON，再跑 `finalize --run-dir "<R>" --receipt "<json>"`，由它统一写 `STATUS.md` 与 `status.json=completed`；不要手工改状态文件。回执校验不通过时状态不会写成 completed，此时按打印的 `problems` 重新上传，不要绕过。

## 十、质量红线（违反即阻止输出）

1. 没有任何真实文字来源时才输出素材卡并停止。**官方章节要点、官方总摘要、作者发布的完整文案都属于真实文字来源**，足以产出正式精选笔记；它们与口播逐字稿只是证据等级不同，不是「缺」。只有连章节 / 文案都拿不到时，才用显式标注「未完成」的素材卡占位。
2. 不把推荐流、同作者其他视频、搜索结果摘要当作本条视频观点。
3. 每条要点带出处；`【原文】/【文案】/【归纳】/【推断】` 四级不得混用，`【平台摘要】` 只在第三节；引用 ≤150 字/段。
4. 未取得的字段写「未取得」，禁止补写、脑补或按常识填值。
5. 入库目标固定为独立知识库 `YOUR_IMA_KB_ID`；该库不存在或不可写时停下报告，不改投「电商」或其他库、不静默写错位置。
6. 上传后必须验证；产物与中间文件放 D 盘，不占用 C 盘。
7. 复用逐字稿必须经 `resolve_transcript.py` 校验母版 SHA256；校验不过就停止，不得把「复用」写成「本次重新转写」。
8. 访客 cookie / 凭证默认不落盘；确需保存时用 `--save-session`，`status.json` 里只准出现 cookie 名称、不准出现值。Windows 上 `os.chmod(0600)` **不等于** ACL 收紧（实测仍是 `0o666`），必须用 `icacls /inheritance:r /grant:r <当前用户>:F`；`dy_http.save_secret` 已实现并返回 `hardened` 布尔值，为 `false` 时必须输出警告而不是宣称「已限权」。
9. **章节要点必须跑 `extract_chapters.py` 再下结论**。`chapter_list` 为 `null` 不等于没有章节；`recommend_chapter_info.recommend_chapter_list` 才是页面章节面板的数据源。禁止只看一个键就把「章节要点原文」写进「未取得字段」，禁止把已取得的章节数据当作缺口报「已完成」。`chapters.json` 的 `checked_paths` 是判定依据。
10. 「未取得」必须可审计：任何写进笔记 / 元信息 / STATUS 的「未取得」，都要能指出查过哪些字段或路径；理由只能来自脚本输出或明确的失败记录，不能来自「上次也没拿到」的惯性。
11. **转写被拒 / 失败必须落成可审计终态**。`transcription_gate.py` 未跑、`status.json` 还停在 `chapters_ready` 且 `next_step` 指向「写笔记」时，禁止进入阶段 3（2026-11-08 实际复现的误导状态）。`no_silent_retry=true` 时禁止静默重试同一工具，也不得换近似工具绕过；要重试必须用户明确授权并重新跑门禁。
12. **短链解析必须可审计、逐跳校验域名**。`resolve_input.py` 的 `input_resolution` 是唯一判定依据：禁止用「取最长数字」猜 video_id（分享链含 21 位 `social_author_id`），禁止把非白名单域名或伪协议（`javascript:` / `data:`）重构成合法 URL，禁止跳过 host 校验直接信任路径里的 id。
13. **入库前必须通过 `lint_note.py`**。`error_count>0` 或退出码非 0 时禁止调用 `create_media`；`lint-note.json` 随产物留存。这条把所有「靠模型自觉」的交付哲学变成硬门禁：2026-11-08 复跑实测，它拦下了首次交付里 11 处 `【平台摘要】` 越界和 1 处章节时间点写错（`00:02` 应为 `00:00`）。
14. **本地离线逐字稿不得冒充准确原文**。sherpa-onnx 对产品名 / 型号名有听写误差（实测 Claude Code→「克劳扣的」、Opus 5.5→「open5.5」、CCswitch→「Cwitch」、skill→「sll」）。引用时：产品名以作者文案 / 官方章节为准，逐字稿原话标 `【原文】` 并附时间戳，明显听写偏差标 `[ASR不确定]`，**禁止**把听写结果当正式名称写进可执行清单；同时不得把「用了离线 ASR」写成「云端工具转写」。`local_asr.py` 的头部与 `local-asr.json` 是溯源依据。
15. **禁止读取、导出、复制用户的浏览器 cookie 或 profile**。为了让请求带上登录态，2026-11-08 试过三条野路子，全部失败且**造成真实损害**：① 离线解密 `Cookies` 库——74 条抖音记录全是 `v20`（App-Bound Encryption），密钥封在 Edge 进程里，DPAPI 只够解出 `Local State` 的主密钥；② 复制 profile 再用 CDP 读——复制成功但 Edge 判定 v20 解不开，`sessionid` 直接消失；③ 用目录联接（junction）指回真实 profile 开调试端口——Chromium 136+ 本来就禁止对默认 user-data-dir 开调试，而绕过它的代价是 Edge 把这 74 条解不开的 cookie 当损坏数据**全部删除**（靠动手前的副本才还原）。因此：不得为拿登录态去读 `Cookies`、不得拿真实 `--user-data-dir` 做调试实验、不得把用户 cookie 写到磁盘。缺登录态时正确动作是**停下来说明**，或另开专用 profile 由用户自己扫码登录。

## 十一、资源清单

| 文件 | 用途 | 何时读 |
| --- | --- | --- |
| `scripts/douyin_detail.py` | **v1.3 默认下载器**：签名直连 + 重定向/重试 + 媒体校验 + 幂等 + 单一状态文件 | 阶段 1C 首选 |
| `scripts/dy_http.py` | 重定向、退避重试、失败分类、媒体魔数校验、限权写 secret、状态合并 | 由下载器与复用脚本引用 |
| `scripts/resolve_transcript.py` | **1A 降级路径**：安全复用已核实逐字稿，强制 SHA256 母版校验 | 1B 被拒/失败/不值得重复时 |
| `scripts/extract_chapters.py` | **v1.4 章节抽取**：多候选字段 + JSON 字符串解包 + 毫秒时间戳 + 漏检审计，产出 `chapters.md/.json` | 阶段 2 必跑 |
| `scripts/resolve_input.py` | **v1.5 输入解析**：分享口令抽 URL、短链展开、逐跳 host 白名单、canonical 去重、失败分类，写 `input_resolution` | 阶段 0 由 fetch 自动调用 |
| `scripts/probe_subtitles.py` | **v1.5 字幕探测**：递归扫 `detail.json` + JSON 字符串解码 + `checked_paths` 审计，区分作者文案与字幕 | 阶段 2.0 必跑 |
| `scripts/transcription_gate.py` | **v1.5 转写门禁**：终态 + fallback/fail 策略 + 交付等级判定 + 本地 ASR 探测，覆盖 `status`/`next_step` | 阶段 1E 必跑 |
| `scripts/lint_note.py` | **v1.6 入库前自检**：自贬措辞 / 证据标记越界 / 占位符 / 章节数 / 时间点 / 章节型误用 `【原文】`，退出码非 0 阻止入库 | 阶段 3 末、阶段 4 前必跑 |
| `scripts/run_pipeline.py` | **v1.7 编排**：`prepare`（0→1C→1B'→2→1E）/ `check`（lint + upload manifest）/ `finalize`（状态与 STATUS，v1.8 起支持 `--receipt` 交叉校验）/ `clean`（v1.8 回收旧 run，语义取自 `nextflow clean`），自动建目录与审计 `pipeline-steps.json` | 首选入口 |
| `scripts/local_asr.py` | **v1.7 本地离线转写**：ffmpeg 16 kHz → silero VAD → sherpa-onnx SenseVoice，产出可被门禁识别的逐字稿；`--probe` 查后端 | `prepare --use-local-asr` 或 1B' |
| `scripts/setup_local_asr.py` | **v1.8 可迁移部署**：幂等安装 sherpa-onnx + SenseVoice + silero VAD，固定 size/SHA256 校验；完整则跳过、半成品拒绝覆盖（需 `--force`）、`--check` 只体检、`--verify` 复核指纹 | 换机部署 / 后端异常排查 |
| `scripts/watch.py` | **v1.10+ 监控台账**：账号清单 + aweme_id 去重 + 六态队列。`scan-dom`（从登录态 Edge 的 DOM 快照文件解析作品卡片并去重入队，v1.15 新增）/ `scan-result`（手拼 items 的备用）/ `scan-search`（v1.16 新增，从全站搜索结果 JSON 入队，按作者 sec_uid 落账）/ `inbox` / `pending` / `mark` / `reconcile`（回收卡死+按产物推状态+重试）/ `forget` / `status`。`extract_works_from_dom` 处理相对路径、标题重复两遍、置顶/共创/纯数字/XX万 四种播放量形态 | 无人值守采集的发现层 |
| `scripts/search_discover.py` | **v1.16 全站搜索发现**：进程内驱动 vendored `DouyinSearcher`（独立 Chromium，非 Edge），归一化后写 JSON 并自动 `watch.py scan-search` 入队；默认 `publish_time=180&sort_type=2`，本地按 `since_days`（默认 30）过滤；**v1.17 加会话预热 + 滑块人工等待 `--human-wait`**，严禁连刷（会触发 verify_check→滑块） | 关键词维度发现，配合 §三·补 关键词搜索 |
| `D:\douyin-distill\_vendor\douyin-api\` | **v1.16 vendored 搜索引擎**：`zhubaohe123/Douyin-api`（MIT）。`douyin_searcher.py` 已加 `publish_time`/`sort_type`/`since_days` 参数；`login.py` 一次性扫码登录；自带 `.venv`（装于 D 盘，不占 C 盘，依赖用清华镜像直链离线装，默认源经代理拉大包会断，见 memory 2026-11-08）。**不直接读 Edge cookie** | 全站搜索的底层引擎 |
| `scripts/run_batch.py` | **v1.10 批量采集**：开跑先 `reconcile`，逐条 `prepare` 到 `collected`，带限速/预算/错误分类/退避重试，结果写 `_watch/batches/<日期>.json` | 凌晨 02:30 |
| `scripts/daily_brief.py` | **v1.10 晨报**：汇总昨夜采集 + **队列实况（待蒸馏/待采集/失败）**，留「这条对我们有什么可用点」待填位；输出 `<日期>-<HHMM>.md` 与 `latest.md` | 早间 09:30 |
| `references/watch-feeds.md` | 发现层实测结论：6 条 HTTP 路线全部被封的证据、浏览器取数方式、限速与降级；§8.1 记录「入库后笔记被改写」为何不能只比字节数；§9 记录关键词搜索与 cookie 两条路为何都走不通 | 改采集逻辑前必读 |
| `tests/test_drift_detection.py` | **v1.11 回归用例**：7 种指纹形状，含「等长改写」（48 vs 48 bytes，内容不同）——旧逻辑判 `published`，新逻辑判 drift。改 `derive_state` 后必跑 | `python tests/test_drift_detection.py` |
| `tests/test_scan_items.py` | **v1.13 回归用例**：`--items` 解析。标题含 `\|` 或 `~` 时旧实现会静默丢视频 / 吞播放量；现按「续块」处理并给出 9 个形状 | 改 `parse_scan_items` 后必跑 |
| `tests/test_scan_queue.py` | **v1.13 回归用例**：同一批扫描里重复的 `aweme_id` 旧实现会重复入队（重复下载 100 MB + 重复蒸馏），现在按唯一 id 折叠并回报 `duplicates_in_scan` | 改 `cmd_scan` 后必跑 |
| `scripts/dy_sign/` | 内置签名包：`abogus.py` / `sm3.py` / `websign.py`（开源实现本地化，仅改相对导入） | 由 douyin_detail.py 自动引用 |
| `scripts/douyin_fetch.py` | 输入归一化、yt-dlp 兜底编排、状态 JSON（旧路径，保留排查用） | 阶段 0 / 1C 兜底 |
| `scripts/upload_presigned.py` | 预签名 URL 的 HTTP PUT 上传 | 阶段 4 |
| `references/douyin-download.md` | 签名原理、开源出处、完整命令、已排除路径与本机环境事实 | 阶段 1C 必读 |
| `references/ima-upload.md` | 目标知识库 ID、三步入库参数、MIME 表、验证与排错 | 阶段 4 必读 |
| `references/distill-spec.md` | 证据分级、笔记结构、禁止项、深度模式 | 阶段 3 必读 |
| `assets/note-template.md` | 正式笔记模板 + 素材卡模板 | 阶段 3 / 降级时 |
