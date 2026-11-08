# 抖音视频下载与文字获取手册

> 仅在阶段 1C 需要在线下载、或下载失败需要排查时加载。

## 1. 结论先行（v1.2，2026-11-08 已跑通）

抖音 Web 的视频直链不是静态 URL，必须先请求：

```
https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=<video_id>
```

拿到 `aweme_detail` 才能拿到 `video.play_addr.url_list`。这个接口是**签名保护**的，完整需要的不是「新鲜 cookie」这么简单：

| 要素 | 来源 | 说明 |
| --- | --- | --- |
| `ttwid` / `UIFID_TEMP` / `enter_pc_once` | **HEAD** 请求 `douyin.com` 的 `Set-Cookie` | GET 只给 `__ac_nonce`（JS 挑战页），HEAD 才发访客身份 |
| `s_v_web_id` | 本地按抖音前端算法生成 | `verify_<base36(毫秒)>_<36 位 UUID-v4 形状>`；单独伪造它不够 |
| `a_bogus` | 纯 Python 复刻（SM3 + RC4 + 自定义 base64） | 对查询串做签名，需与 UA / browser_info 一致 |
| `verifyFp` / `fp` | 等于 `s_v_web_id` | 放进查询参数 |
| `uifid` | 等于 `UIFID_TEMP` | 放进查询参数和 header |
| `x-secsdk-web-signature` | `md5("{uifid}_{秒级时间戳}_{SALT}_{query}")` | SALT = `A96D855A08C0A9707F8BEF0D9A527E4E` |
| `msToken` | 本地随机 126 字符 + `==` | 抖音对该端点不校验真伪 |

**关键认知修正**：`a_bogus` 一直能被接受，之前失败的根因是漏了 `uifid` + `x-secsdk-web-signature` 这组访客参数，以及用 GET 而不是 HEAD 去拿 cookie。旧结论「必须绕开下载」已作废——用 `scripts/douyin_detail.py` 可以直接下载。

## 2. 三条路径（按成功率排序）

### A. 用户给字幕 / 文稿（最稳）

抖音播放页「字幕 / 文稿 → 复制」，或直接给 `.srt / .vtt / .txt / .md`。存 `source/transcript.md` 后直接进入阶段 2。

### B. 本地视频文件转写（推荐）

用户在抖音 App 里保存视频到相册、或录屏，把文件放到工作目录。调用音频转写工具传本地绝对路径（支持 mp4 / mp3 / m4a / wav 等，≤1 GB / 5 小时）。完全不需要 cookie 与签名。

### C. 在线下载（v1.2 首选，签名直连）

```bash
cd "D:/douyin-distill/<id>"
python "<skill-dir>/scripts/douyin_detail.py" <id> --download
```

产物：`api/detail.json`（元信息 + **章节要点**）、`api/play_urls.json`、`api/detail_raw.txt`、`api/<id>.mp4`；状态只写顶层 `<workdir>/status.json`（v1.3 起**不再**产 `api/status.json`），访客凭证默认**不落盘**（`--save-session` 才写 `session.json` 且收紧 ACL）。`status.json` 里 `failure_class=ok` 且 `has_aweme_detail=true` 才算成功。

`api/detail.json` 不只是元信息：页面「章节要点：共 N 个」的原文就在里面，阶段 2 必须对它跑 `scripts/extract_chapters.py`（见 §9）。

成功后抽音轨再转写，7 分钟视频约 3 MB，比直接传 108 MB 的 mp4 省时：

```bash
ffmpeg -hide_banner -loglevel error -y -i "api/<id>.mp4" -vn -ac 1 -ar 16000 \
  -c:a libmp3lame -b:a 64k "api/<id>.mp3"
# 再对 api/<id>.mp3 调用音频转写工具，结果存 source/transcript.md
```

脚本用 `http.client` 直连（绕开本机注册表代理打断 `urllib` TLS 的坑），无需登录、无需 Edge、无需 `yt-dlp`、无需复制 Cookie DB。UA 与 `browser_version` / `browser_info` 必须保持一致，改 UA 就要同步改参数模板。

<details><summary>旧路径 yt-dlp（仅作兜底排查，不要再当默认）</summary>

```bash
python "<skill-dir>/scripts/douyin_fetch.py" "https://www.douyin.com/video/<id>" \
  --workdir "D:/douyin-distill/<id>"
yt-dlp --cookies-from-browser "edge:D:/douyin-distill/edge-profile" \
  -f "best[height<=720]/best" -o "D:/douyin-distill/<id>/%(id)s.%(ext)s" \
  "https://www.douyin.com/video/<id>"
```

`edge-profile` 需含 `Default\Network\Cookies` 与 `Local State`；Edge 运行时会独占锁住 Cookie DB。
</details>

## 3. 已排除的做法（2026-11-07 实测，属旧路径，勿重复踩坑）

| 做法 | 结果 |
| --- | --- |
| 匿名 `yt-dlp` | `Fresh cookies needed` |
| `curl -I` 拿 `ttwid` / `enter_pc_once` / `UIFID_TEMP` 合并进 cookie jar | 仍失败；**缺的不是 `ttwid`** |
| 伪造 `s_v_web_id=verify_<hex>` | 错误从 `expected` 变成 `please report this issue`，接口仍拒绝（缺 `a_bogus`） |
| `yt-dlp --cookies-from-browser edge` | `Could not copy Chrome cookie database`，Edge 运行时独占 Cookie DB |
| PowerShell `[IO.File]::Open(..., FileShare.ReadWrite)` 复制 Cookies DB | 同样被独占锁拒绝；`Local State` 可正常复制 |
| `iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=<id>` | HTTP 200 但 0 字节，老接口已下线 |
| `iesdouyin.com/share/video/<id>/` | 重定向回 Web，返回约 72,914 字节 `_$jsvmprt` JS 挑战页，需 `__ac_signature` |
| `curl` GET `douyin.com/video/<id>` | 同样是 JS 挑战页，无正文 |
| 浏览器直接打开 detail API | 空响应（缺 `a_bogus`） |
| 浏览器地址栏注入 `javascript:` 读 `document.cookie` | 浏览器剥离 / 拦截，未执行 |
| WebFetch 抓 `douyin.com/root/search/...?modal_id=` | 返回「搜索结果为空」；必须把 `modal_id` 转成 `/video/<id>` |

## 4. 页面元信息的兜底抓法

即使下载失败，元信息通常仍可取：

- **Edge 扩展浏览器**：`browsers_list` 取 browser_id → 打开 `/video/<id>` → `dom_cua_get_visible_dom` 拿话题标签、作者主页链接，`tab_screenshot` 读播放器下方文案、互动数据、发布时间、「章节要点：共 N 个」的计数。
- **静态抓取兜底**：`WebFetch` 抓 `https://www.douyin.com/video/<id>`，抖音精选 SEO 页会输出标题、作者、截断文案、部分互动数与同作者视频列表。
- **视频停在 `00:00 / 00:00` 只代表 CDN 未加载、浏览器面板打不开，不代表章节要点不存在**：播放器打不开时改走 §9 的接口抽取。旧版本在这里直接写「N 条章节原文未取得」是错误结论，已作废。

浏览器工具注意：`cua_execute` 的 `keypress` **不支持组合键**（`Control+L` 报 `unsupported key`），组合键要用 `dom_cua_keypress`；`dom_cua_get_visible_dom` 常过滤掉主视频区，需配合截图读取。

## 5. 本机环境事实

- C 盘长期吃紧，所有产物放 `D:\douyin-distill\`。
- 系统注册表代理 `127.0.0.1:10810` 时通时断；WorkBuddy 内部代理端口每次会话漂移（出现过 33867 / 37352 / 8099）。命令行网络操作前清除代理环境变量：
  ```bash
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY <command>
  ```
  `curl` 在本机加 `--ssl-no-revoke --noproxy '*' --http1.1`。
- Python `urllib` 会自动读注册表代理，可能被掐断 TLS 握手；上传脚本因此用 `http.client` 直连。
- Edge 走注册表代理，页面 HTML 能开不代表视频 CDN 能加载。
- 浏览器一律 Edge（用户长期偏好）。

## 6. 校验与降级

下载完成后核对：文件存在、大小 > 0、后缀是受支持媒体格式、能被转写工具接受。校验失败按「未取得文字」处理。

全部失败时套用 `assets/note-template.md` 的素材卡部分，记录已核实原文 + 失败复盘 + 三条补全路径，然后停止。

## 7. 开源出处（借鉴记录，2026-11-08）

`scripts/dy_sign/` 与 `scripts/douyin_detail.py` 的算法与参数模板来自以下 GitHub 项目，仅做本地化（相对导入改写、参数硬编码、去异步依赖），未改算法：

| 项目 | 复用的内容 | 对应本地文件 |
| --- | --- | --- |
| `Evil0ctal/Douyin_TikTok_Download_API` | `src/dtk/signing/native/abogus.py`（a_bogus / SM3 / browser_info） | `dy_sign/abogus.py` |
| 同上 | `src/dtk/signing/native/sm3.py` | `dy_sign/sm3.py` |
| 同上 | `src/dtk/signing/native/websign.py`（x-secsdk-web-signature + SALT） | `dy_sign/websign.py` |
| 同上 | `src/dtk/signing/native/signer.py` 的 `_add_web_signature` 拼接顺序 | `douyin_detail.py` |
| 同上 | `src/dtk/platforms/douyin/params.py` 的 `base_params` 与 `AID/VERSION_CODE` | `douyin_detail.py` |
| `Johnserf-Seed/TikTokDownload` | `Server/s_v_web_id.js` 的 `verify_fp` 生成算法 | `douyin_detail.py:create_s_v_web_id` |
| `xrqi/tiktok_to_knowledge` | 同类「视频 → 知识库」流水线的 cookie 处理思路（仅参考） | — |

该项目的签名文档还给出两个值得记住的实测结论：

- 抖音 `a_bogus` 被接受但请求仍失败时，先看 `403 Uifid Not Found` / `Signature Not Found`，缺的是访客参数而不是签名算法。
- 签名与 cookie jar 必须描述**同一个访客**（`verifyFp` 必须等于该 jar 里的 `s_v_web_id`），否则平台返回空 payload。

**注意**：签名算法与 `version_code` 会随抖音 Web 发版变化。若 `status.json` 变成 `ok=false` 且 `http_status=200` / 空 body，优先回上游仓库比对 `params.py` 的 `VERSION_CODE` 与 `websign.py` 的 `SALT`，不要退回盲试 cookie。`SIGNING_CONSTANTS` 已集中在 `douyin_detail.py` 顶部，`status.json` 会原样带出，方便比对。

## 8. 健壮性修复（v1.3，2026-11-08）

复跑时审计出的真实漏洞，以及从 GitHub 借来的对应方案：

| 漏洞（复现证据） | 借来的方案 | 本地实现 |
| --- | --- | --- |
| `play_urls.json` 第 3 条返回 **302**，旧代码只认 200 且只试前 3 条 → 静默跳过 | yt-dlp `downloader/http.py` 的 Location 跟随 | `dy_http.request` 支持 301/302/303/307/308 + 相对 Location，最多 5 跳；默认试 6 条 URL |
| 落盘只判断 `>100_000` 字节，错误页/截断文件会被写成 `.mp4` | yt-dlp 的 Content-Length / Range 一致性检查 | `validate_media`：校验 `ftyp` / `ID3` 魔数、Content-Length、HTML/JSON 错误页前缀，通过才写盘并记 SHA256 |
| 403 / 空 body 只给通用 `response_preview`，分不清「签名失效」和「访客被风控」 | `dtk/transport/classify.py` 的有序命名规则 + `signing/protection.py` 的 fail-safe 提示 | `dy_http.classify` 输出 `signature_refused / risk_control / withheld_payload / network_error` 等，`status.json` 带 `failure_class` + `hint`，明确指向 `VERSION_CODE` / `SALT` |
| 网络抖动无重试，失败即止 | yt-dlp `RetryManager.report_retry` 指数退避 | `dy_http.request` 对网络异常 / 5xx 退避重试；**4xx 不重试**，避免拿同一签名反复撞风控 |
| 顶层 `status.json` 与 `api/status.json` 两份状态冲突 | 上游 `dtk/ops/pipeline.py` 的单一请求状态记录 | `merge_status`：全程只维护 `<workdir>/status.json`，各阶段合并写入 |
| `cookies.json` 明文存 `UIFID_TEMP`(224 字符) / `ttwid`(127 字符)，无开关 | 上游身份池「cookie 属于凭证」的约定 | 默认不落盘；`--save-session` 才写 `session.json`；`status.json` 只留 cookie 名称 |
| Windows 上 `os.chmod(0600)` 实测仍 `0o666`，「限权」是假的 | —（本机回归实测） | `dy_http.save_secret` 在 NTFS 调 `icacls /inheritance:r /grant:r <用户>:F`，返回 `hardened`；失败则输出 `session_warning`。`icacls` 输出是 GBK，必须按字节捕获再解码，否则 `text=True` 会抛 `UnicodeDecodeError` |
| 每次重跑都新建访客身份、重下 108 MB | 上游 identity pool 的身份复用 | `--save-session` 复用 visitor jar；已存在且通过魔数校验的 mp4 直接跳过（`--force` 覆盖） |
| 转写被拒绝/失败时流程无标准降级路径 | —（Skill 自身缺口的补丁） | `scripts/resolve_transcript.py`：拒绝素材卡、强制母版 SHA256 一致，带溯源说明复用逐字稿 |

**排错顺序**：先看 `status.json` 的 `failure_class` → 按 `hint` 比对 `SIGNING_CONSTANTS` → 再看 `download_attempts` 里每条 URL 的 `redirects` / `validation.reason`。不要一上来就换 cookie 或伪造 `s_v_web_id`。

## 9. 章节要点在 detail.json 里（v1.4，2026-11-08 用户指出后修复）

**事故复盘**：连续 4 轮笔记都写「章节要点原文未取得（`chapter_list` 为空）」，同时报「已完成」。实际 `api/detail.json` 早已下载到本机，只是查错了字段。这是**漏检**，不是数据缺口。

| 字段 | 实测值 | 说明 |
| --- | --- | --- |
| `aweme_detail.chapter_list` | `null` | 旧字段，PC/Web 常常为空 |
| `aweme_detail.anchors` | `null` | 同上 |
| `aweme_detail.video_text` | `[]` | 同上 |
| **`aweme_detail.recommend_chapter_info.recommend_chapter_list`** | **5 条** | **页面「章节要点：共 5 个」的真实数据源** |
| `aweme_detail.recommend_chapter_info.chapter_abstract` | 128 字 | 官方总摘要 |
| `aweme_detail.recommend_chapter_info.chapter_recommend_source` | `2` | 平台推荐来源标记 |
| `aweme_detail.douyin_p_c_video_extra` | `{"ai_summary":1,"ai_summary_v2":1,...}` | 该视频已开启 AI 摘要能力（字符串 JSON，需二次解码） |

单条章节结构：`{"desc": "变现机会的衡量", "detail": "通过监控多个账号……", "timestamp": 120000}`。`timestamp` 单位是**毫秒**：`2000→00:02`、`17000→00:17`、`120000→02:00`、`196000→03:16`、`335000→05:35`，与 `video.duration=419237 ms` 及逐字稿段落位置吻合。早期用「>100000 才算毫秒」的朴素规则把前两条误写成 33:20 / 283:20，已改为按视频时长判定单位。

抽取命令与产物：

```bash
python scripts/extract_chapters.py --workdir "D:/douyin-distill/<id>/run6"
# -> source/chapters.md（总摘要 + 章节表 + 逐条【平台摘要】原文 + 字段检查清单）
# -> source/chapters.json（count / timestamp_unit / checked_paths）
# -> status.json 合并 chapters 段
```

脚本按优先级检查 9 个候选路径，支持 JSON 字符串二次解码，并**强制输出 `checked_paths` 审计清单**。只有 `ok=false`（退出码 3）且审计表显示所有候选为空，才允许写「章节要点未取得」。

**写作规范**：章节要点是平台 AI 摘要，不是口播逐字稿；笔记中用 `【平台摘要】` 标记，独立成节，不与 `【原文】` 混淆。详见 `references/distill-spec.md` §2 / §3。

## 10. v1.5：分享短链与转写单点故障（2026-11-08 GitHub 调研后修复）

触发场景：用户贴来整段抖音分享口令「9.74 复制打开抖音… https://v.douyin.com/JeNBuAWxQn0/ 06/05 Jvf:/ B@t.eO :1pm」，随后转写工具两次被工具审批拒绝。整条链路停摆，且 `status.json` 仍写着 `chapters_ready` / `next_step = 去写笔记`——断点续跑会产出一份没有逐字稿的「正式笔记」。

### 10.1 借来的实现

| 上游 | 文件 | 复用的设计 |
| --- | --- | --- |
| `Evil0ctal/Douyin_TikTok_Download_API` | `src/dtk/urls/expand.py` | `first_url(text)`、`MAX_REDIRECTS=5`、**每一跳重新校验 host 白名单**、canonical 去重；注释明确写「抖音链最后两跳会 normalize 成同一资源，早先当成循环导致最常见的分享链接全部失败」 |
| 同上 | `src/dtk/urls/patterns.py` | `SHORT_LINK_HOSTS` / `BARE_URL_HOSTS` 白名单 |
| 同上 | `src/dtk/urls/parse.py` | `resolve()` 作为唯一入口，接受 raw share text / bare URL |
| 同上 | `src/dtk/services/transcriptions.py` | `TERMINAL_STATES=("done","failed","cancelled")`、`NORMALIZATION_FAILURE_POLICIES={"fallback","fail"}`、`as_dict(reused=...)` —— **失败是终态，不是静默继续** |
| 同上 | `documents/zh/18-funasr-models.md` | 完全离线的 FunASR ONNX 三件套（Paraformer-large + FSMN-VAD + CT-Transformer，16 kHz 量化） |
| `ggml-org/whisper.cpp`(55k★) / `SYSTRAN/faster-whisper`(26k★) / `k2-fsa/sherpa-onnx`(16k★) / `modelscope/FunASR`(21k★) | — | 本地 ASR 备选后端清单 |

### 10.2 复现的漏洞 → 方案 → 本地实现

| 漏洞（复现证据） | 借来的方案 | 本地实现 |
| --- | --- | --- |
| 原始分享口令喂进阶段 0 返回 `invalid_input`，还让用户「提供 15-25 位 id」 | `first_url(text)` + 短链展开 | `scripts/resolve_input.py`；`douyin_fetch.py` 自动调用，`status.json` 写 `input_resolution` 审计 |
| 分享链含 21 位 `social_author_id` 与 19 位 `mid`，「取最长数字」会拿错 | 从 `/video/<id>`、`/share/video/<id>` 路径取 id | `VIDEO_PATH_RE` 优先，`PARAM_ID_RE` 次之，裸 id 只在整串匹配时才接受 |
| 起点 / 跳转不校验域名，`https://evil.example.com/video/<id>` 会被当合法输入（回归实测） | 起点 + 每跳 `assert_allowed` | `ALLOWED_HOSTS` + `redirect_not_allowed`；`javascript:` / `data:` 伪协议单独拦截（也是回归实测抓到的） |
| 转写被拒后状态仍是 `chapters_ready`、next_step 指向写正式笔记 | `TERMINAL_STATES` + 显式失败策略 | `scripts/transcription_gate.py` **覆盖**顶层 `status` / `next_step`，输出唯一 `deliverable` |
| 「抖音也许有官方字幕」只能靠猜 | 结构化字段探测 | `scripts/probe_subtitles.py`：递归 + JSON 字符串解码 + `checked_paths`；`desc`/`caption` 硬标非字幕 |
| 单点依赖云端 ASR，被拒即无路可走 | `fallback` 策略 + 多后端 | 门禁按 `逐字稿 > 官方字幕 > 章节要点 > 元信息` 自动降级；本地后端探测写入 `local_backends` |

### 10.3 关于离线转写（可选，未自动安装）

本机实测 2026-11-08：`faster_whisper` / `funasr` / `sherpa_onnx` / `whisper` / `torch` / `transformers` **全部未安装**，只有 `ffmpeg` 可用。C 盘剩 4.6 G、D 盘 118 G，量化中文模型约几百 MB～数 GB。

若以后要在工具被拒时也能离线转写，按优先级：① `sherpa-onnx` + SenseVoice（最轻，ONNX，无 torch）② `faster-whisper` small/medium ③ FunASR ONNX 三件套（中文最强，但三份模型要按文档目录结构放）。**模型一律放 D 盘**，装好后 `transcription_gate.py` 的 `local_backends` 会自动显示可用；不需要改 Skill 流程。当前版本只是把这条路记录清楚，**不擅自下载模型**。

### 10.4 回归结果（2026-11-08）

- `resolve_input`：8/8 断言通过（原始口令、裸短链、direct URL、modal_id、纯 id、无 URL、恶意跳转域名、非法起始域名、两跳链、重定向循环、offline、伪协议）。
- 阶段 0 用原始口令实跑：`ready_for_signed_download`，`input_type=short_link`、`hops=1`、id `7702918823682395446`。
- `probe_subtitles` 对真实 `detail.json`：11 个候选路径全空，唯一 sweep 命中 `music.lyric_short_position` 是歌词位置；`ok=false`、退出码 3、审计完整。
- `transcription_gate`：5/5 交付等级与状态覆盖正确；`declined` 时 `no_silent_retry=true`、`terminal=true`，`next_step` 指向「按官方章节 + 作者文案正常写精选笔记」。
- 真实任务 `status.json` 已从误导状态改为 `limited_note_allowed` 终态。

## 11. v1.6：第二次从零复跑抓到的交付缺陷（2026-11-08）

用整段分享口令在全新 `run2/` 目录重跑 v1.5，链路本身一次通过（短链 1 跳、mp4 SHA256 与首次逐字节一致、章节 11 条、门禁终态正确、入库 REPLACE 验证成功）。**问题全部出在「写笔记」这一步，而且都是文档规则靠模型自觉没能兜住的**：

| # | 缺陷 | 根因 | v1.6 处理 |
| --- | --- | --- | --- |
| 1 | 首次交付的逐条原文第 1 章时间点写成 `00:02`（应为 `00:00`） | 从另一条视频的笔记复制粘贴，没有任何交叉核对 | `lint_note.py` 用 `chapters.json` 逐条比对章节表 / 逐条原文的时间点与标题 |
| 2 | 「核心要点」「可执行清单」里出现 11 处 `【平台摘要】` | 规格写了「只允许第三节」，但无人校验 | lint 按第三节行区间硬拦截 |
| 3 | 互动数沿用早一版抓取值 | 未强制取当次 `detail.json` | `meta.md` 固定记抓取时点；笔记统一取本次 `statistics` |
| 4 | 节名「来源与缺口」/「来源与证据」在两个文件里不一致 | 模板、规格、实际笔记各写各的 | 全文统一为「来源与证据」 |
| 5 | 实际笔记用了 `【文案】`，但规格的三级证据表没定义它 | 标记体系滞后于实践 | 规格补第四级 `【文案】`；lint 拒绝未定义标记 |
| 6 | `SKILL.md` 阶段 1D 仍写「有限笔记模式 + 顶部标缺口」，与红线 1 直接冲突 | v1.5 只改了部分段落，旧条款残留 | 全文统一术语，禁止把证据等级差异写成缺口 |
| 7 | 阶段 0 用 `tee` 在 `mkdir` 之前写文件报 `No such file or directory` | 命令靠临时拼装 | `SKILL.md` 阶段 0 给出可整段复制的命令骨架（先 `mkdir -p`，用 `--json-out`） |
| 8 | 模板 `{{字段路径}}` 被静默替换成空串，知识库里留下「来自 （…）」无出处句 | 占位符填空不校验 | lint 增加 `empty_source_path` 检查 |

`lint_note.py` 回归：首次交付笔记被拦下 **12 处错误**（11 处标记越界 + 1 处时间点），run2 版笔记 **PASS / 0 error / 0 warning**；另构造 5 个负向用例（自贬措辞、占位符、未定义标记、章节型误用 `【原文】`、错误时间点）全部被拦截。

> 结论：下载 / 签名 / 章节 / 门禁 / 入库这条工程链路已经稳定，v1.6 的打磨重点不在「再换一个下载方案」，而在**把交付质量规则做成机器门禁**。

## 12. v1.7：本地离线 ASR（去掉云端转写单点依赖）

### 12.1 为什么做

`AudioTranscribe` 曾被工具审批连续拒绝两次，整条链路只能降级。视频文件其实就在 `api/` 里，缺的只是「不依赖外部工具就能把音频变成文字」的能力。v1.7 把它装在本机，**全程离线、不占 C 盘**。

### 12.2 部署（v1.8：一条命令，可迁移）

**换机只需一句**，不用再手抄下面的手工步骤：

```bash
python scripts/setup_local_asr.py --check     # 先体检：python / ffmpeg / 磁盘 / 目标可写 + 逐文件指纹
python scripts/setup_local_asr.py             # 幂等安装（完整则跳过、半成品拒绝覆盖）
python scripts/setup_local_asr.py --verify    # 复核已装文件的 SHA256
python scripts/setup_local_asr.py --force     # 强制重下（唯一会覆盖已有文件的开关）
```

设计取自 `mm7h/XiaoZhi.Net` 仓库的 `Download-SherpaModels.ps1`（非本 Skill 文件）：**完整则跳过、半成品则拒绝、重做要显式 `--force`、下载前先做能力检查**。三条原则解决的都是同一类事故——半截包被当成好模型继续用，或者一次失败重试把旁边的好文件覆盖掉。

| 安全属性 | 行为 | 实测（2026-11-08） |
| --- | --- | --- |
| 已完整 | 跳过，不发起任何请求 | `--check` 三个工件 `state=ok`，退出码 0 |
| 半成品（体积不足） | **拒绝**，不覆盖，提示加 `--force` | 塞 1,000 bytes 的 `tokens.txt`，输出 `refused=true`、`state=partial`，文件仍是 1,000 bytes |
| 指纹不符 | 判 `digest_mismatch`，同样拒绝 | 未构造（逻辑与上一行同分支） |
| 就绪判定 | 只有「三工件 + 模块 + numpy 全 OK」才 `ready=true` | 半成品树 `ready=false`，退出码 3 |

固定指纹（换机后必须逐字节一致）：

| 文件 | bytes | SHA256 |
| --- | --- | --- |
| `model.int8.onnx` | 239,233,841 | `c71f0ce0…9a2cd51` |
| `tokens.txt` | 315,894 | `f449eb28…429a1dc` |
| `silero_vad.onnx` | 643,854 | `9e2449e1…bb1fd6` |

**源按「中国可直连」优先排序**：`hf-mirror.com` → `huggingface.co` → GitHub release 的 `.tar.bz2`（用 Python `tarfile` 解包，避开部分 Windows 自带 `tar.exe` 缺 bzip2 的问题）。网络层由脚本自己处理：清掉 `http(s)_proxy` 环境变量，再显式 `curl -x 127.0.0.1:10810 --ssl-no-revoke -C -` 逐代理重试，成功即记录用了哪个出口。

以下为本机首次手工安装的原始记录，保留用于对照：

| 项 | 值 |
| --- | --- |
| 安装根目录 | `D:\WorkBuddy-ASR\`（C 盘当时仅剩 4.48 GiB，D 盘 116 GiB） |
| Python | 3.13.14，pip 26.1.2 |
| 运行时 | `pip install --target D:\WorkBuddy-ASR\site sherpa-onnx==2.0.1 numpy>=2.1`（`sherpa_onnx-2.0.1-cp313-cp313-win_amd64.whl` 2.1 MiB + `sherpa_onnx_core` 16.7 MiB + numpy 12.6 MiB） |
| ASR 模型 | SenseVoice `model.int8.onnx` 239,233,841 bytes + `tokens.txt` 315,894 bytes，来自 HF `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` |
| VAD 模型 | `silero_vad.onnx` 643,854 bytes，来自 GitHub release `k2-fsa/sherpa-onnx` 的 `asr-models/silero_vad.onnx` |
| 音视频转换 | 本机 ffmpeg 9.0.1 |

**本机网络坑（重要）**：PyPI 直连 200，但 GitHub release / HuggingFace 直连超时，必须走 `127.0.0.1:10810`；同时环境变量里的 `47977` 代理对部分域名返 502。下载用：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  curl --ssl-no-revoke -L -C - --retry 4 \
  -x http://127.0.0.1:10810 -o model.int8.onnx "<HF resolve URL>"
```

pip 也要避开注册表代理打断 TLS 的老问题：`NO_PROXY='*' no_proxy='*'` + `PIP_CACHE_DIR=D:\WorkBuddy-ASR\pip-cache`，并 `env -u` 清掉四个代理变量。

### 12.3 实现要点（`scripts/local_asr.py`）

- `OfflineRecognizer.from_sense_voice(model, tokens, num_threads=2, use_itn=True, language="zh")`；`accept_waveform(16000, float32_array)` 接受 `Sequence[SupportsFloat]`，用 numpy 最稳。
- **必须用 VAD 分段**：第一版用「能量静音切分」实测 60 秒音频切成 35 段，其中十几段是背景噪声被幻觉成「。」「嗯」「我」，还有 25 秒长段挤在一起。换成 `VoiceActivityDetector` + silero 后同样 60 秒切成 5 段，全是完整句子，耗时反而从 6.3 秒降到 4.4 秒。
- 再加 `usable_text()` 过滤纯标点 / 少于 4 字 / 汉字少于 3 且无 3 位以上字母数字的行，作为二次兜底。VAD 模型缺失时自动回退能量切分，不让它成为新单点。
- 输出 `[m:ss.mmm,m:ss.mmm] 文本`，与云端工具格式一致，`resolve_transcript.looks_like_transcript` 能识别。`looks_like_transcript` 对带本地 ASR 溯源头部的稿子放宽为「≥3 条时间戳行 + ≥200 字符」，避免短视频被 1000 字符门槛误杀（素材卡仍被 `SOURCE_CARD_MARKERS` 挡住）。
- `--probe` 只查就绪状态；`transcription_gate.probe_backends()` 会调它，把 D 盘安装写成 `sherpa_onnx_sensevoice_local.available=true`。

### 12.4 实测（run3，从零全链路）

| 指标 | 值 |
| --- | --- |
| 命令 | `python run_pipeline.py prepare "<整段分享口令>" --use-local-asr` |
| 音频 | 20 分 22 秒（1,222.25 秒）mp4 → 16 kHz 单声道 PCM |
| 分段 / 有效文字 | silero VAD 77 段 / 71 段有可用文字 |
| 逐字稿 | 22,657 bytes，带毫秒时间戳 |
| 转写耗时 | 87.47 秒（CPU 2 线程），整条 prepare（含下载 110 MB）1 分 47 秒 |
| 门禁 | `state=done`、`deliverable=full_note`、`local_asr.ok=true`，**未调用任何云端 ASR** |
| 交付 | `run_pipeline.py check` → `PASS / 0 error` → 17,847 bytes 正式笔记入库 REPLACE，`parse_progress=100` |

### 12.5 已知误差与使用规则

SenseVoice 中文语义可用，但产品名 / 型号名听写明显失真：Claude Code→「克劳扣的 / 克劳蔻的」、Opus 5.5→「open5.5 / ops5.5」、CCswitch→「Cwitch」、Codex→「codeex / codedex」、skill→「sll」。因此：

1. 产品名以**作者文案与官方章节**为准，逐字稿写法只作听写留痕；
2. 引用逐字稿原话标 `【原文】` + 时间戳，明显偏差标 `[ASR不确定]`；
3. 禁止把听写结果当正式名称写进可执行清单，禁止把「离线 ASR」说成「云端工具转写」；
4. 视频里的演示样例文案（本条 15–16 分钟混入「玉石之路 / 匈奴」）不是课程知识点，笔记里单独标注，不写进核心要点。

> 模型与运行时都在 D 盘，`local_asr.py` 只按约定路径查找，不自动下载、不写 C 盘、不改动系统 Python 环境。换机器时按 §12.2 重新部署即可。
