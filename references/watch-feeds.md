# 监控采集：发现层怎么打通

> 场景：不只处理「你贴来的那条链接」，而是每天凌晨自动发现监控账号的新视频，下载、转写成逐字稿，早上给出取舍判断。
> 本文件记录**哪些路线实测可用、哪些已死**，避免下次重新踩一遍。

## 1. 结论先说

**发现层只能用真实浏览器做。** 所有基于 HTTP 的账号作品列表路线在本机（2026-11-08）全部被封：

| 路线 | 结果 | 判读 |
| --- | --- | --- |
| `/aweme/v1/web/aweme/post/`（带完整 a_bogus + x-secsdk 签名） | HTTP 200，**0 字节** | 静默风控拦截，body 为空不是网络问题 |
| 同上 + `verifyFp` / `fp` / Referer / 全套列表参数 | HTTP 200，**0 字节** | 加参数无效 |
| `iesdouyin.com/web/api/v2/aweme/post/` | HTTP 200，`status_code=11110` | 明确要求登录 |
| `douyin.com/web/api/v2/aweme/post/` | HTTP 200，`status_code=11110` | 同上 |
| `iesdouyin.com/aweme/v1/web/aweme/post/` | HTTP 403 | 直接拒 |
| `douyin.com/user/<sec_uid>` 的 HTML | HTTP 200，6.4 KB，含 `captcha`/`verify` | 返回验证页，无 SSR 作品数据 |

对照：**`/aweme/v1/web/aweme/detail/` 对游客仍然开放**，带同一套签名 HTTP 200、`status_code=0`、数据完整。所以分工是：

```
浏览器  → 发现（拿到 aweme_id）
详情接口 → 元信息 / 章节 / 下载（已证明可用）
```

不要试图给 `/aweme/post/` 补签名或补 cookie —— 签名本身是对的（同一套签名在 detail 上成功），封的是这个端点的匿名访问。

## 2. 浏览器侧怎么取

- 浏览器用 **Microsoft Edge**（`browsers_list` 里 `family=edge` 的那个），扩展版本 0.2.8 实测可用。
- 打开 `https://www.douyin.com/user/<sec_uid>`，`dom_cua_get_visible_dom` 读一次可见 DOM 即可。
- 作品卡片形如：

  ```html
  <a href="/video/7678555174301879593">1.6万碰到我的 AI，你的钱包算是倒霉了... #这很容易 …</a>
  ```

  链接文字开头是**播放量**（`1.6万` / `872` / `共创1.5万`），后面是标题与话题。置顶作品会带 `置顶` 前缀。
- 首屏约 12 条作品，足够覆盖「当天发布」；不要再往下滚，滚动收益低且更容易触发风控。
- 账号主页同时给出「作品23」这类总数，可用于判断是否有大量历史存量。

**不要**把整段 DOM 塞给脚本解析。让模型从 DOM 里挑出 `aweme_id` 列表，再交给 `watch.py scan-result`；脚本只做去重与台账，这样浏览器那侧换实现也不影响脚本。

## 3. 数据与命令

全部落在 `D:/douyin-distill/_watch/`（C 盘零占用）：

| 文件 | 作用 |
| --- | --- |
| `accounts.json` | 监控清单：sec_uid / 昵称 / 主页 / 上次扫描时间 / 上次新增数 |
| `seen.json` | 见过的每个 aweme_id（含首见时间、状态），**去重的唯一依据** |
| `queue.json` | 采集队列：queued / processing / done / failed / skipped |
| `inbox.txt` | 手工投递兜底，一行一个链接或分享口令 |
| `batches/<日期>.json` | 每晚批量采集的结果明细 |
| `brief/<日期>.md` | 晨报底稿 |

```bash
S="YOUR_SKILL_DIR/scripts"

python "$S/watch.py" add "<主页链接或 sec_uid>" --nickname "这很容易"
# 带标题与播放量扫（推荐）：'id~标题~播放量|id~标题~播放量'
python "$S/watch.py" scan-result --account "<sec_uid>" --items "id~标题~播放量|id~标题~播放量"
python "$S/watch.py" inbox
python "$S/watch.py" pending
python "$S/watch.py" reconcile            # 修台账：回收卡死 / 按产物推状态 / 重试
python "$S/watch.py" mark <aweme_id> --status distilled --note-file "<笔记文件>"
python "$S/watch.py" forget <aweme_id>   # 误入队 / 想重新采集
python "$S/watch.py" status

python "$S/run_batch.py" --limit 8 --budget-minutes 90
python "$S/daily_brief.py"
```

## 3.1 状态机（v1.10 起）

```
queued ──► processing ──► collected ──► distilled ──► published
              │            (逐字稿就绪)   (笔记过 lint)   (入库并验证)
              └──► failed (retryable / permanent / stale)
```

**`collected` 是凌晨任务允许停下的终点**，只有早间任务能把它推进到 `distilled` / `published`。v1.9 只有一个 `done`，一次真实批量之后 4 条 `done` 里有 3 条根本没有笔记——台账替不存在的成果作了证。拆开之后，`watch.py status` 的 `needs_distillation` 就是「这轮还欠几篇笔记」。

`reconcile` 干三件事，都抄自成熟系统：

| 动作 | 抄自 | 解决的事故 |
| --- | --- | --- |
| `processing` 超过 30 分钟回收为 `queued` | Celery `visibility_timeout` | 进程被杀（睡眠/中断/超预算）后条目永久卡死，再也不会被处理 |
| 按磁盘产物重新判定状态 | Nextflow `-resume`、yt-dlp archive | 台账字段由会死的进程写入；笔记文件在不在才是事实 |
| 可重试失败自动回队，最多 3 次 | Celery `autoretry_for` + 退避 | 偶发超时直接判死，人得手动捞 |

**为什么不重试所有失败**：`404` / 视频不存在 / 已删除重试三次只是把失败拖慢，还会掩盖真因。所以先分类（`classify_error`），`permanent` 不重试。

## 4. 节奏

| 时间 | 任务 | 干什么 | 为什么这样切 |
| --- | --- | --- | --- |
| 02:30 | 凌晨采集 | 扫账号 → 入队 → 下载 → 本地 ASR 转写 | 全是确定性工作，无人值守也能跑完 |
| 09:30 | 早间判断 | 出晨报 → 逐条判断 → 值得的写笔记 → lint → 入库 | 写笔记要模型判断，放在人在的时候 |

合并成一次也行，但写完笔记就没人复核；分开跑的好处是**晨报先于笔记**——先看清单决定取舍，再花时间蒸馏，不做无用功。

## 5. 限速与规模

- 单夜上限 **8 条**（`run_batch.py --limit 8`），时间预算 90 分钟（`--budget-minutes`）。
- 账号之间间隔 ≥15 秒，条目之间默认 20 秒（`run_batch.py --gap`）。
- 参考吞吐：一条 16 分 43 秒的视频，下载 + 62 段转写共 **95 秒**。8 条约 13 分钟，远在预算内。
- 视频里那套「监控 200 多个博主」是常驻服务的量级；本机是单机串行，现实规模是**十几到几十个账号**。超过这个量要么加间隔、要么拆成多批次跨夜跑。

## 6. 降级路径

浏览器不可用（扩展断开、Edge 未登录、出现验证码）时：

1. **不要**去试 HTTP 接口，已实测全封。
2. 只用 `watch.py inbox`：把白天看到的链接丢进 `inbox.txt`，凌晨照样自动下载转录。
3. 报告里必须写明降级原因，不能假装扫过了。

这条兜底不是凑数——它保证「浏览器这层挂了，采集链路仍然成立」，只是发现要靠人投递。

## 7. 已排除的想法

- **从 Edge 的 cookie 库里偷 cookie 给脚本用**：Windows 下是 DPAPI 加密，要解就得拿用户凭据，越权且脆弱。不做。
- **给 `/aweme/post/` 补更完整的设备指纹**：签名已经对了（detail 用同一套成功），封的是端点匿名访问，参数补不出权限。
- **用搜索引擎 / 第三方聚合站拿作品列表**：时效性差，拿不到当天发布，等于没有采集。

## 8. 自检记录：跑一遍才发现的问题（v1.9 → v1.10）

2026-11-08 用 v1.9 真跑了一遍完整链路（浏览器扫描 → 入队 → 批量 3 条 → 晨报），暴露 9 处不足，全部已修。记在这里是因为**这些问题读代码看不出来，只有跑才现形**。

| # | 不足 | 怎么发现的 | 修法 |
| --- | --- | --- | --- |
| 1 | `done` 同时表示「采集完成」和「已入库」 | 跑完批量后查台账：4 条 `done` 里 3 条没有笔记文件 | 拆成 `collected` / `distilled` / `published`，`collected` 是凌晨任务的终点 |
| 2 | `processing` 卡死不可恢复 | 手工置 `processing` 后跑 `--dry-run`，待处理数 = 0 | `reconcile` 按 `started_at` 超 30 分钟回收（Celery visibility_timeout） |
| 3 | 失败原因记成 `}` | 投一个不存在的 id，台账 `reason` 字段就是 `}` | 改为解析 `run_pipeline` JSON 的 `error` 字段，再退到 stderr 末行 |
| 4 | 失败即终态，无重试 | 同上，一次失败直接 `failed` | `classify_error` 分 retryable/permanent，可重试的最多 3 次，退避 60s→180s |
| 5 | `title_hint` 13/13 全空 | 晨报里全是「（无标题记录）」 | `scan-result --items 'id~标题~播放量'` 入库时带标题；已下载的从 `detail.json` 回填 |
| 6 | 晨报只看昨夜成功项，积压与失败看不见 | 晨报里 grep 不到 `queued`/`failed` | 新增「队列实况」小节：待蒸馏 / 待采集 / 失败待处理，含失败原因 |
| 7 | 同日晨报互相覆盖 | `brief/` 只有一个按日期的文件 | 文件名带 `HHMM`，并额外写一份 `latest.md` 给自动化引用 |
| 8 | `run_batch` 输出是多段 JSON 流 | 想用 `json.load` 消费，报 `Extra data` | 加 `--json-out`，进度与总结分开；同日多次运行自动归档旧文件 |
| 9 | `--limit` 按插入序，旧积压饿死新内容 | 代码审阅 | 加 `--order newest|oldest`，默认新内容优先 |
| 10 | **入库后的笔记被本地改写，判定看不出来** | 一条 `distilled` 明明已入库却不升 `published`；查 `status.json` 记 `verified_file_size=19328`，磁盘上只剩 19102 bytes，mtime 比 `status.json` 晚 2 小时 | 见下节：改用内容哈希比对，并把漂移显式报出来 |
| 11 | **模板和 lint 一起把使用者的类目写死了** | 生成的笔记第七节直接叫「与业务的关联（家纺四件套 / 抖店商品卡 / 自然流）」；`assets/note-template.md` 里连占位符都是 `{{商品卡 / 自然流 / 选品 / 客服}}` | 见下节：改成中立的「可迁移点」，场景只允许来自 `profile.md` 或用户当次说明 |

顺带修掉两个自己写出来的 bug：

- `reconcile` 的 `save()` 原本以 `changes` 非空为条件，导致「标题回填」这类字段级修复只改了内存没落盘（表现是回填后标题依然为空）。
- `run_batch` 记录失败原因时取 `stdout` 最后一行，对 JSON 输出等于取到 `}`。
- `run3b` 这类带后缀的 run 目录名不被 `run(\d+)` 全匹配，`clean` 会静默漏掉（v1.8 已修，此处合并记录）。

### 8.1 第 10 处展开：为什么「比对字节数」不够

v1.10 判断「已入库」用的是这段逻辑：

```python
legacy_ok = (ima.get("verified_file_size") == note_bytes
             and ima.get("put_status") in (200, 201, 204))
```

这等价于 Nextflow 的 `cache 'lenient'`——只看大小（有时还看 mtime）。Nextflow 自己的文档把这套模式的短板写得很直白：

> `cache 'lenient'` hashes file size+timestamp (path) instead of content — ... **it can miss same-size content changes**. `cache 'deep'` hashes file content.

也就是说：**只要改写后的长度碰巧相同，旧判定就会说「已验证」**。这次之所以还判对了，纯粹是因为 19328 ≠ 19102；换个改法就漏了。DVC 把同一件事叫 `modified`（“the corresponding file hash in the `.dvc` file is not up to date”），并且**它是要报出来的**，不是静默降级。照这个改：

| 改动 | 内容 |
| --- | --- |
| 写入侧 | `run_pipeline.py finalize` 自己算 `note.sha256` 与 `ima.verified_note_sha256`。v1.10 这个字段是**人手填进 `status.json` 的**，所以只有填过的那一次才有——元数据由人维护，等于没有 |
| 读取侧 | `watch.py content_matches()`：有 sha 就比 sha（deep），无 sha 才退回比 size（lenient），并**在证据里注明是哪种比法** |
| 状态侧 | 指纹不符 → 判 `distilled` + `⚠ 本地笔记已被改写`，附两个 sha 前缀与字节数；`reconcile` 另写 `note_drift` 字段供晨报引用 |
| 终态 | `published` 不再视为不可重判。v1.10 把它当终态，于是「入库后又被改」这件事永远发现不了 |

回归用例 `_watch/tests/test_drift_detection.py` 覆盖 7 种形状，其中第 1 例是**等长改写**（48 bytes vs 48 bytes，内容不同）——旧逻辑判 `published`，新逻辑判 drift。

写这个测试时踩到一个坑：`Path.write_text()` 在 Windows 上会做换行转换，`\n` 落盘变 `\r\n`，于是「算哈希用的字节」和「文件里的字节」不是一回事，所有用例一开始全判成 drift。测试里必须 `newline=""`。

### 8.2 第 11 处展开：模板不该替使用者决定他做什么生意

笔记第七节原本叫「与业务的关联（家纺四件套 / 抖店商品卡 / 自然流）」，`lint_note.py` 的 `REQUIRED_SECTIONS` 又把它列成必需小节。两件事叠起来的后果是：**任何人用这个 skill 蒸馏任何一条视频，都会被逼着往「家纺四件套 / 抖店」上套**，不套就 lint 不过、不能入库。

这跟「不要妄自猜测用户业务方向」是同一条原则的两个面。上一轮只改了自动化 prompt 和 `profile.md`，漏了模板和 lint——说明**规则写在文档里没用，得写进会报错的那一层**。

修法：

| 位置 | 改动 |
| --- | --- |
| `assets/note-template.md` | 第七节改「可迁移点」；占位符变成 `{{这条方法能用在什么环节}}`；并写明「场景由 `_watch/profile.md` 或用户当次说明决定，不要替他假定类目」；无从判断时要求写「本条不迁移：与当前场景无交集」 |
| `scripts/lint_note.py` | `REQUIRED_SECTIONS` 该项改成别名元组 `("与业务的关联", "可迁移点", "与我的场景的关联", "对本场景的用处", "适用场景")`；匹配循环支持 tuple |
| `references/distill-spec.md` §3.7 | 同上，注明「旧称『与业务的关联』」 |
| `SKILL.md` 阶段 3.5 | 同上，并强调 profile 为空时不猜 |

验收：旧标题笔记 → PASS；改成「可迁移点」→ PASS；把整节删掉 → `BLOCK UPLOAD` + `section_missing`。别名没有把检查吞掉。

## 9. 关键词搜索：两条路都试过了，都不通

「盯指定账号」回答不了「全抖音最近新出了什么」。要在全站范围内找内容，必须走搜索——而这条路对游客是**关闭**的，不是限流：

| 尝试 | 结果 |
| --- | --- |
| 搜索页 URL 传 `?publish_time=30&sort_type=1` | 参数被前端剥掉，最终只剩 `?type=general` |
| 搜索页 UI 点「最新发布 / 半年内」 | 游客态结果区**永久空白**，只剩加载动画（与 `/aweme/post/` 静默风控同源）；**登录态下正常**，返回全是 1–6 天前的新视频 |
| 搜索页 HTML | 72 KB 纯 CSR，无 `RENDER_DATA` / `__INITIAL_STATE__` |
| `dom_cua_get_visible_dom` 提取卡片 | 只返回导航层，拿不到结果卡片与 `video_id`（连屏幕上清晰可见的 AI 回答正文也不在快照里） |
| `Evil0ctal/Douyin_TikTok_Download_API` | README 明写 *"Search and the posts under a hashtag... Douyin refuses both to a guest, **so neither is offered there**"* |
| 热搜榜 `/aweme/v1/web/hot/search/list/` | ✅ HTTP 200 / 65 KB / 50 条，但**全是时事**（「导弹击中伊朗」），与选题无关 |

### 9.1 接口本身没问题，缺的只是登录身份

2026-11-08 用本地签名（`a_bogus` + 可选 `x-secsdk`）重放 `/aweme/v1/web/general/search/single/`：

```
HTTP 200 · 127 bytes
{"log_pb":{"impr_id":"..."},"status_code":2483,"status_msg":"请先登录，再继续搜索吧"}
```

这是**判定性证据**：签名机制完全正常（否则不会走到「请先登录」这一步），与 `/aweme/post/` 的 0 字节静默风控**性质不同**。顺带确认两件事：

- `x-secsdk-web-signature` 对这个接口**不是必需的**——去掉后仍返回 2483，说明卡点只有身份，不是签名完备性。
- `msToken` 不是卡点——去掉、换随机值，返回都一样。

### 9.2 但登录态在手也没用：顶层导航会被判 `blocked`

把带签名的接口地址**直接用浏览器打开**（登录态下）返回：

```
{"log_pb":{...},"status_code":0,"status_msg":"blocked"}
```

排除法做完了，全部无效：去掉筛选（`publish_time=0&sort_type=0`）、去掉 `msToken`、加回 `x-secsdk`、换 Edge 真实 UA 与 `browser_version=157` —— 一律 `blocked`。

结论：**风控认的是请求上下文，不是参数**。真实客户端是页面内 XHR（`Sec-Fetch-Mode: cors`），顶层导航（`Sec-Fetch-Mode: navigate`）本身就是异常信号。所以「拼一个 URL 让浏览器去打开」这条路在设计上就不通，必须让请求从页面 JS 里发出。

### 9.3 cookie 侧同样封死（而且碰了会毁数据）

要让请求从页面里发出，得先拿到登录态。三条都不行：

| 做法 | 结果 |
| --- | --- |
| 离线解密 Edge 的 Cookies 库 | 74 条抖音 cookie **全是 `v20`**（App-Bound Encryption），密钥封在 Edge 进程里；`DPAPI` 只能解出 `Local State` 的 32 字节主密钥，对 v20 无效 |
| 复制 profile 到别的目录再用 CDP 读 | 复制成功，但 Edge 判定 v20 解不开 → **只返回 8 条新生成的游客 cookie**，`sessionid` 缺失 |
| 用 junction 指回真实 profile + 开调试端口 | Chromium 136+ **禁止对默认 user-data-dir 开调试**（真路径下端口永不出现；换成非默认路径才通）。用 junction 绕过这个限制的代价是灾难性的：**Edge 把这 74 条解不开的 v20 当损坏数据全部删除** |

最后一条是踩过的坑，必须写下来：**不要拿用户的真实 profile 做 ABE 实验**。动手前先 `Copy-Item Cookies` 留一份原始库，出事后可逐字节还原（本次就是这样救回来的：74 条、`sessionid`/`sid_tt`/`UIFID`/`passport_csrf_token` 齐全）。

### 9.4 现状与正确做法

- 不要为了「全站发现」去装第三方付费搜索 API，也不要顺手去读用户的登录 cookie。
- 现阶段唯一可行的覆盖全站方式：**人工投递** `_watch/inbox.txt`（一行一个链接），或在 `accounts.json` 里补几个同赛道账号，让账号维度去逼近「全站」。
- 若将来一定要做，正确形态是**用户自己的浏览器 + 一次后台登录**（专用 profile，扫码一次，不用碰系统 profile），然后在页面上下文里发请求。前提是那台机器上允许常驻一个有登录态的自动化浏览器。

顺带记两条平台事实，将来真要重做时能省一轮试错：`publish_time` 只接受 `0 / 1 / 7 / 180`（全部 / 一天 / 七天 / 半年），**没有「30 天」**，要「近一个月」只能拉 180 再按 `create_time` 本地过滤；`sort_type=1`（最多点赞）首屏给的是 5–14 个月前的旧片，热度排序不反映新鲜度，要新内容只能用 `sort_type=2`。

### 9.5 账号主页 DOM 验证（2026-11-08 实测，发现层打通的关键一步）

§9 讲的是「全站搜索」走不通；但「盯指定账号」这条路在登录态下**是通的**，且比预想好用。同日用 `family=edge` 的登录态实例打开账号主页（`/user/<sec_uid>`），`dom_cua_get_visible_dom` 的快照行为：

| 项 | 游客态 | 登录态 |
| --- | --- | --- |
| 作品卡片数 | **1 条**（被全屏扫码弹窗遮挡，快照按可见性过滤掉其余） | **12 条**（首页 `node_id 34–45` 连续的 `/video/<id>` 卡片） |
| 页头「作品」数 | — | 23，即首屏只覆盖约一半 |
| 格式 | `/video/<id>` + `置顶42.0万` + 标题 | 同左，且播放量前缀有多种形态 |

快照里每条卡片形如：
`<a node_id=34 href="/video/7682633651724569891">置顶42.0万抄底拼多多…#这很容易…#投资</a>`

解析时要处理的真实怪象（全部已写进 `watch.py:extract_works_from_dom`，并由 `scan-dom` 子命令消费）：

1. **链接是相对路径** `/video/...`（非 `https://...`）——解析只抽 19 位数字 id，前缀无所谓。
2. **标题在 `<a>` 文字里整段重复两遍**（完整标题 + 话题出现两次）——取第一个 `#` 之前作为标题，否则标题会被翻倍。
3. **播放量前缀形态多**：`置顶42.0万` / `共创1.8万` / `1937`（纯数字无单位） / `1.6万`——正则整体捕获，前缀（置顶/共创）保留以便调用方跳过置顶旧片。
4. **首屏不全**：登录态首屏 12 条 vs 页头 23 作品。cua_scroll 在此环境**连续超时不可用**，滚动补齐不可靠；但抖音作品默认最新在前，首屏 ~12 条覆盖近期足够，「今夜新发」不会漏在首屏外。故凌晨任务取首屏即可，不滚动。

**结论**：账号维度的发现层已可用——`dom_cua_get_visible_dom` 存文件 → `watch.py scan-dom --account <sec_uid> --dom-file <文件>` 一步完成解析+去重+入队。剩下两个未决项见 §10。

## 10. 仍未解决

- **浏览器扫描的账号上限没测过**。目前只用 1 个账号验证过；十几个账号串行扫描的耗时与风控表现未知。首夜实跑后应把实测值补到这里。
- **`seen.json` 只增不减**。单机规模下无害（1 万条约几 MB），但跨年会变成垃圾堆；真要长期跑应加「早于 N 个月且非 published 的记录归档」。
- **没有账号级健康度**。现在能看出某条视频失败，看不出「这个账号连续 7 天没产出」（可能改名/被封/停更）。`accounts.json` 只记了 `last_scan_at` 与 `last_new_count`，缺趋势。
- **笔记漂移只报不改**。检测到「入库后被改写」时，脚本只把状态降回 `distilled` 并写 `note_drift`，**不会自动重传**。原因是重传要走 ima 的 OAuth 通道（只有宿主进程有 token），且会覆盖知识库里已有条目，属于有副作用的写操作，不该由定时任务擅自决定。目前需要人工跑一次 `check → 上传 → finalize --receipt`。若要自动化，得先在 ima 侧确认「同内容重传是否幂等」。
- **旧 run 没有 sha 可比对**。v1.11 之前入库的笔记只在 `verified_file_size` 里留下大小，这些记录只能按大小比对，等长改写仍可能漏。属于历史数据局限，无法追溯补齐（原版本已被覆盖）。
