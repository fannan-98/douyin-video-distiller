# douyin-video-distiller · 抖音视频精选

> 把**一条**抖音视频变成**一份**带出处、可追溯的结构化蒸馏笔记，并存入 ima 独立知识库「抖音视频精选」。

这是一个 [WorkBuddy](https://www.workbuddy.cn/) 用户级 Skill（`name: douyin-video-distiller`，当前版本 **v1.17**）的源码快照。文档里沉淀的是一套完整工程链路：**解析输入 → 取得真实文字 → 抓取元信息与官方章节 → 按证据分级蒸馏 → 入库前自检 → 上传知识库并验证**，外加断点续跑、无人值守采集与回归测试。

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `SKILL.md` | 主文件：触发条件、固定配置、决策树、五个阶段、质量红线、资源清单。**读这一个就够上手** |
| `assets/note-template.md` | 正式笔记模板 + 素材卡（降级）模板 |
| `references/distill-spec.md` | 证据分级、笔记结构、禁止项 |
| `references/douyin-download.md` | 接口签名原理、开源出处、下载命令、已排除路径 |
| `references/ima-upload.md` | 目标知识库配置、三步入库参数、验证与排错 |
| `references/watch-feeds.md` | 发现层实测结论：哪些路被封、为什么、如何降级 |
| `scripts/run_pipeline.py` | **首选入口**：`prepare` / `check` / `finalize` / `clean` |
| `scripts/douyin_detail.py` | 默认下载器：内置签名包直连 detail 接口 + 媒体校验 + 幂等 |
| `scripts/dy_sign/` | 内置签名包 `abogus.py` / `sm3.py` / `websign.py` |
| `scripts/local_asr.py`、`setup_local_asr.py` | 本地离线转写（ffmpeg + silero VAD + sherpa-onnx SenseVoice），幂等部署 |
| `scripts/lint_note.py` | 入库前自检，退出码非 0 阻止入库 |
| `scripts/watch.py`、`run_batch.py`、`daily_brief.py`、`search_discover.py` | 发现 / 批量采集 / 晨报 / 关键词搜索 |
| `scripts/upload_presigned.py` | 预签名 URL 上传 |
| `tests/` | 漂移检测、扫描解析、队列去重等回归用例 |

## 怎么用

在 WorkBuddy 中把本目录作为 skill 加载后，直接贴一条抖音链接（支持 `modal_id` 搜索链接、`/video/<id>`、分享短链、整段分享口令）并说：

```
跑抖音精选 <链接>   /   下载并蒸馏   /   放进抖音视频精选
```

只贴链接也会触发。端到端的阶段 4 依赖 ima MCP 的 OAuth 通道（token 属于宿主进程），因此**不是纯离线脚本**：采集、蒸馏、校验可脚本化，入库与验证由 agent 执行、脚本校验回执。

命令行入口示例：

```bash
python scripts/run_pipeline.py prepare "<抖音链接或整段分享口令>" --run-dir "<workdir>"
python scripts/run_pipeline.py check --run-dir "<workdir>"
python tests/test_drift_detection.py
```

## 运行前提

- Python 3.13+；标准库为主，本地 ASR 需 `ffmpeg`、`sherpa-onnx` 及 SenseVoice 模型（可用 `setup_local_asr.py --check` 体检）。
- WorkBuddy + ima 知识库 MCP（写入目标知识库）。
- 网络：下载与接口请求走 `http.client` 直连，绕开本机注册表代理对 `urllib` TLS 的干扰；若环境不同需自行调整代理配置。

## 使用前配置（必读）

公开版已把作者的个人标识替换为占位符，用之前请全部换成自己的值：

| 占位符 | 含义 | 怎么填 |
| --- | --- | --- |
| `YOUR_IMA_KB_ID` | 主目标知识库（笔记写入处） | 在 ima 客户端新建一个**独立知识库**，用 `get_addable_knowledge_base_list` 查到 ID 后替换 |
| `YOUR_IMA_KB_FALLBACK_ID` | 备用知识库 | 仅在你明确指定「改投另一个库」时使用；不需要可忽略 |
| `YOUR_IMA_KB_OTHER_ID`、`YOUR_FOLDER_ID_A`、`YOUR_FOLDER_ID_B` | 排错记录里出现的其他库 / 文件夹 | 只是历史排查记录，不用替换，也不影响主链路 |
| `YOUR_SKILL_DIR` | 本机 skill 目录的绝对路径 | 指向你 clone 下来的目录（到 `scripts/` 的父目录） |
| `YOUR_USER` | 原作者的 Windows 用户名 | 换成你自己的用户名 |

脚本里还有几处硬编码的**路径常量**（不是隐私，但换机要改）：

| 常量 | 出现位置 | 说明 |
| --- | --- | --- |
| `D:/douyin-distill` | `run_pipeline.py` 的 `DISTILL_ROOT`、`watch.py` / `run_batch.py` / `daily_brief.py` / `search_discover.py` 的 `WATCH_ROOT`、`douyin_fetch.py` 默认 workdir | 产物根目录。直接改常量，或每次用 `--run-dir` / `--workdir` 覆盖 |
| `D:/WorkBuddy-ASR` | `local_asr.py`、`setup_local_asr.py`、`run_batch.py`、`run_pipeline.py` 的 `--asr-root` | 离线 ASR 安装根目录；`setup_local_asr.py` 支持 `WORKBUDDY_ASR_ROOT` 环境变量 |
| `D:/douyin-distill/_vendor/douyin-api` | `search_discover.py` 的 `VENDOR_DIR` | 可选的全站搜索引擎，**不在本仓库内**；不用关键词发现可不装 |

环境变量（`run_pipeline.py` 会把知识库 ID 写进 manifest，未设置会写入占位符导致入库失败）：

```bash
export IMA_KB_ID="你的知识库ID"            # Windows PowerShell: $env:IMA_KB_ID="..."
export WORKBUDDY_ASR_ROOT="/path/to/asr"   # 可选
```

## 质量红线（摘要）

- 拿不到**真实文字来源**时必须停下并输出素材卡，**禁止**凭标题、话题或记忆臆造内容。
- 禁止把推荐流、同作者其他视频的内容当作本条视频的观点。
- 笔记保留来源链接与作者，引用保持简短并标注出处。
- 不读取、不复制、不解密浏览器登录 cookie；会话凭证默认不落盘。
- 不用于批量爬取、绕过付费/登录墙，或下载他人受版权保护内容用于再发布。

## 说明与许可

- 本仓库为**公开**存档，用于版本管理与换机迁移。接口签名与平台策略会变，文档记录的是 2026-11-08 的实测状态，使用前请复核。
- `references/` 里保留了大量作者本机的排错记录（磁盘容量、代理行为、Edge profile 等）。这些是**开发笔记**，不是通用前提，换机后按上面的配置清单处理即可。
- **第三方代码**：`scripts/dy_sign/` 与 `scripts/douyin_detail.py` 的签名实现取自上游开源项目，许可证与文件对应关系见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及 `references/douyin-download.md §7`。
- **本仓库其余内容未声明统一许可证**：可以阅读与 fork 自用；未明确授予再分发、商用许可。若希望别人合法复用，请补一个 `LICENSE` 文件，并确认与上游 Apache-2.0 / MIT 条款兼容。
- 内容涉及第三方平台接口的分析，仅用于个人学习与内容沉淀，请勿用于批量爬取、绕过登录墙或再发布他人受版权保护的内容。
