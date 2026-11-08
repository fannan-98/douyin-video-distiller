# ima 知识库入库规范

> 阶段 4 必读。所有 ID 与状态均于 2026-11-08 实测核实。

## 1. 目标位置

| 项 | 值 |
| --- | --- |
| 知识库名称 | 抖音视频精选 |
| `knowledge_base_id` | `YOUR_IMA_KB_ID` |
| 目标位置 | 该知识库**根目录**，调用 `add_knowledge` 时**不传 `folder_id`** |
| 权限 | `can_add_knowledge: true`（2026-11-08 实测；当前 2 条笔记，非新建空库） |
| 备用知识库 | 「电商」`YOUR_IMA_KB_FALLBACK_ID`，仅当用户明确指定时使用 |

**结构澄清（重要）**：「抖音视频精选」是用户 2026-11-08 新建的**独立知识库（一级）**，不是「电商」里的子文件夹。

- 2026-11-07 曾按「文件夹」理解，在「电商」根目录两页 + 「某业务知识库」子目录里翻找，均无结果。
- 2026-11-08 通过 `get_knowledge_base_list(KBT_MINE_KB)`、`get_addable_knowledge_base_list`、`search_knowledge_base("抖音视频精选")` 三个入口确认它是一级知识库，`get_knowledge_list(YOUR_IMA_KB_ID)` 返回空列表且 `current_path` 末级为该库本身。
- 因此**不需要解析 folder_id**，也无需在库内再建子文件夹。

可选前置校验：调用 `get_addable_knowledge_base_list` 确认 `YOUR_IMA_KB_ID` 在可写列表里。库不存在或不可写时停下报告，**不改投「电商」或其他知识库**。

**v1.6 交付前硬门槛（必做）**：先跑 `python scripts/lint_note.py --note "<笔记>" --workdir "<workdir>" --json-out "<workdir>/lint-note.json"`，确认 `error_count=0`、退出码 0、`gate` 不是 `BLOCK UPLOAD`。未通过就改笔记，**禁止**带着自贬措辞、证据标记越界或章节时间点错误上传。lint 报告随产物留存。

**v1.7 introduction 显示坑（实测）**：ima 用 `get_knowledge_list` 返回的 `introduction` 是**净化后的纯文本摘要**，不是 markdown 原文。它会剥掉行内代码（反引号内容）以及字段名里的部分下划线。2026-11-08 实测同一份笔记：

| 笔记里写的 | introduction 里显示 |
| --- | --- |
| `` 来自 `aweme_detail.recommend_chapter_info.recommend_chapter_list`（…） `` | `来自 （抖音平台 AI 章节摘要…）` ← 反引号内容整段消失 |
| `来自详情接口字段 aweme_detail.recommend_chapter_info.recommend_chapter_list` | `来自详情接口字段 awemedetail.recommendchapterinfo.recommendchapter_list` ← 下划线被吞 |

所以**笔记头部的关键出处一律用自然语言写**（「来自抖音详情接口的官方章节要点字段」），精确键名放正文第三节的 `> 数据来源` 行；正文不受影响。早期那条 `7678555174301879593` 笔记 introduction 出现「来自 （…）」不是模板没填，就是这个清洗规则造成的，v1.7 已按此重传修正。

## 2. 三步入库序列

### 2.1 create_media — 申请 media_id 与上传方式

```json
{
  "knowledge_base_id": "YOUR_IMA_KB_ID",
  "apply_upload_mode": 1,
  "file_name": "【WorkBuddy-YYYY-MM-DD】<video_id>-<slug>.md",
  "file_ext": "md",
  "file_size": 19328,
  "content_type": "text/markdown"
}
```

- `file_size` 必须是文件**实际字节数**，服务端据此校验上限，填错会导致后续签名校验失败。
- `content_type` 必须按扩展名精确填写，禁止用 `application/octet-stream` 兜底。

常用 MIME 对照（照抄，勿猜）：

| 扩展名 | content_type |
| --- | --- |
| md / markdown | `text/markdown` |
| txt | `text/plain` |
| pdf | `application/pdf` |
| docx | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| xlsx | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| csv | `text/csv` |
| png / jpeg / webp | `image/png` / `image/jpeg` / `image/webp` |
| mp3 / m4a / wav | `audio/mpeg` / `audio/x-m4a` / `audio/wav` |
| html | `text/html` |

对照表找不到的扩展名应拒绝上传。

### 2.2 上传文件内容

`upload_mode = 1`（`UPLOAD_MODEL_URL`，预签名直传）时，响应里给 `upload_url.presigned_url` 与 `upload_url.auth_headers`：

- 对 `presigned_url` 发起 **HTTP PUT**，请求体是文件二进制。
- **原样携带 `auth_headers` 中的全部 header**（含 `Content-Type` 与 `Content-Length`）。
- `Content-Length` 必须与 2.1 的 `file_size` **完全一致**，否则签名校验失败。
- 链接在 `upload_url.url_expired_ts`（秒级时间戳）前有效，超时需重新 `create_media`。
- 此模式**不返回** `cos_credential`、也没有 `cos_key`，不要再向用户索要或拼接 COS 信息。

用封装脚本执行：

```bash
python "<skill-dir>/scripts/upload_presigned.py" \
  --file "D:/douyin-distill/<id>/<note>.md" \
  --url "<presigned_url>" \
  --headers-json '<auth_headers 的 JSON 字符串>' \
  --content-type "text/markdown"
```

若返回 `upload_mode = 2` 或未返回该字段，改为用 `cos_credential` 的 STS 临时凭证（token / secret_id / secret_key）经 COS SDK 上传，目标 key 为 `cos_credential.cos_key`；此分支较少见，需按响应实际字段处理。

### 2.3 add_knowledge — 入库

```json
{
  "knowledge_base_id": "YOUR_IMA_KB_ID",
  "media_id": "<create_media 返回的 media_id>",
  "duplicate_name_strategy": "DUPLICATE_NAME_STRATEGY_REPLACE"
}
```

**不要传 `folder_id`**——目标是独立知识库的根目录。误传一个不存在的 folder_id 会导致入库失败或落错位置。

重名策略：`REPLACE` 用于重跑同一条视频覆盖旧版；`SAVE`（默认）保留历史版本；`CANCEL` 遇重名取消。推荐对同一 video_id 用 `REPLACE`，避免库里堆同一条视频的多个版本。

## 3. 验证

```json
{"knowledge_base_id":"YOUR_IMA_KB_ID","limit":50,"cursor":""}
```

确认返回的 `knowledge_list` 里出现目标 `title`，且 `media_state` 解析成功（值为 2 / `parse_progress` 100）。未出现时报告失败，**不要重复上传**造成重复条目，先查错误原因。

### 3.1 把验证结果交给机器核对（v1.8，推荐）

「我看了列表，在里面」不算证据——肉眼最容易漏的正是**上传了上一版草稿**（文件名一样、字节数不一样）。把 `get_knowledge_list` 的**原始返回**存成 JSON，交给 `finalize` 交叉校验：

```bash
# 原样保存 MCP 返回，不要手工摘字段
python run_pipeline.py finalize --run-dir "<R>" --receipt "<R>/ima-receipt-raw.json"
```

`finalize` 会逐项比对，任一不符即 `RECEIPT MISMATCH` 且**不写** `status=completed`：

| 检查项 | 不接受的情况 | 拦住的事故 |
| --- | --- | --- |
| 文件名 | 列表里没有这篇（按全名或去扩展名比对） | `add_knowledge` 其实没成功 |
| 字节数 | 与本地笔记 `st_size` 不一致 | 上传了旧版草稿 |
| `parse_progress` | < 100 | 以为入库完成、其实还在解析 |
| PUT 状态码 | 非 200/201/204 | 预签名 URL 过期或鉴权失败 |

校验明细落盘 `ima-receipt.json`（原始回执 + 判定结果），随时可复查。**为什么不脚本化整个阶段 4**：ima 是远程 OAuth MCP（`https://ima.qq.com/mcp`），access token 属于宿主进程；脚本侧自建 token 通道既脆弱又越权，所以选择「agent 执行 + 脚本校验回执」这条可审计的边界。

## 4. 命名规范

沿用用户知识库里既有的蒸馏产物命名 `【WorkBuddy-2026-11-03】nofx-DIGEST.md`：

```
【WorkBuddy-YYYY-MM-DD】<video_id>-<slug>.md
```

- `<slug>` 用短英文或拼音，概括主题，如 `ai-token-cost`、`ceo-agent-team`、`bedding-card-flow`。
- 深度模式会产出多份文件，全部加同一日期前缀与 `<slug>-` 前缀，便于在该知识库内聚成一组。
- 素材卡（降级产物）用 `<video_id>-SOURCE-CARD.md`，同样入该库，但标题里带「素材卡 / 未完成」字样，避免被误读为已完成蒸馏。

## 5. 常见错误

| 现象 | 原因 / 处理 |
| --- | --- |
| `invalid GetKnowledgeListReq.Limit` | `limit` 取值范围是 `(0, 50]`，传 100 会报 `code:51` |
| 签名校验失败 | `Content-Length` 与 `file_size` 不一致，或漏带 `auth_headers` |
| 文件类型被拒 | `content_type` 用了兜底值或写错扩展名 |
| 入库后查不到 | 先查 `add_knowledge` 返回；不要立刻重传，避免重复条目 |
| 目标库不可写 | 用 `get_addable_knowledge_base_list` 复核；停下报告，不改投其他库 |

## 6. 历史排查备注（写别的知识库时也适用）

- `get_knowledge_list` 带 `filters: MEDIA_TYPE_FILTER_TYPE: ["FOLDER"]` 会返回**空列表**（即使根目录确有文件夹），该过滤不可用；要找文件夹必须不带 filter 翻页，按 `media_type == 99` 且 `folder_info.name` 筛选。
- 「电商」`YOUR_IMA_KB_FALLBACK_ID`（73 条）根目录超过 50 条，需两页：`cursor=""` → `cursor="CDI="`。其唯一文件夹是「某业务知识库」`YOUR_FOLDER_ID_A`，内含 `wiki` 子文件夹 `YOUR_FOLDER_ID_B`。
- 「另一个个人知识库」`YOUR_IMA_KB_OTHER_ID`（26 条）是另一个个人库；共享 / 我发布的 / 我加入的知识库均 `can_add_knowledge=false`，只读，不能当写入目标。
- MCP 工具集没有「创建文件夹」接口；只有「创建知识库」由用户在客户端完成。遇到「找不到文件夹」时先确认对方是不是新建成了独立知识库。
