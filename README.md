# douyin-video-distiller · 抖音视频精选

> 把**一条**抖音视频变成**一份**带出处、可追溯的结构化蒸馏笔记，并存入 ima 独立知识库「抖音视频精选」。

[WorkBuddy](https://www.workbuddy.cn/) 用户级 Skill（`name: douyin-video-distiller`，v1.17）的源码。完整链路：**解析输入 → 取得真实文字 → 抓取元信息与官方章节 → 按证据分级蒸馏 → 入库前自检 → 上传知识库并验证**，外加断点续跑、无人值守采集与回归测试。

**许可：Apache-2.0**（见 [LICENSE](LICENSE)；第三方代码见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）。

## 快速开始

```bash
git clone https://github.com/fannan-98/douyin-video-distiller.git
cd douyin-video-distiller

cp .env.example .env
# 编辑 .env，只填一项：IMA_KB_ID=你的 ima 独立知识库 ID
set -a; source .env; set +a        # Git Bash；PowerShell 写法见 .env.example

python scripts/doctor.py           # 一键体检，看到「配置就绪」即可
```

然后把本目录作为 WorkBuddy skill 加载，贴一条抖音链接，说「跑抖音精选」。`doctor.py` 不写任何文件，只打印生效配置、检查目录可写性与可选依赖。

> **只需要填一个变量。** 所有路径由 `scripts/dy_config.py` 给出跨平台默认值（Windows 有 D 盘时用 `D:/douyin-distill`，否则 `~/douyin-distill`），不装任何额外组件也能跑主链路。

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `SKILL.md` | 主文件：触发条件、固定配置、决策树、五个阶段、质量红线、资源清单。**读这一个就够上手** |
| `scripts/dy_config.py` | **配置中心**：所有路径与知识库 ID 的唯一来源，读环境变量 |
| `scripts/doctor.py` | **一键体检**：打印生效配置、检查依赖与可写性 |
| `.env.example` | 全部环境变量示例（`.env` 已被 gitignore） |
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
| `LICENSE`、`THIRD_PARTY_NOTICES.md` | 许可证与第三方代码出处 |

## 怎么用

在 WorkBuddy 中把本目录作为 skill 加载后，直接贴一条抖音链接（支持 `modal_id` 搜索链接、`/video/` 加数字 id、分享短链、整段分享口令）并说：

```
跑抖音精选 <链接>   /   下载并蒸馏   /   放进抖音视频精选
```

只贴链接也会触发。命令行入口：

```bash
python scripts/run_pipeline.py prepare "<抖音链接或整段分享口令>" --run-dir "<workdir>"
python scripts/run_pipeline.py check --run-dir "<workdir>"
python tests/test_drift_detection.py
```

## 配置

**唯一必填 `IMA_KB_ID`**：在 ima 客户端新建一个**独立知识库**，用 `get_addable_knowledge_base_list` 查到 ID 填进去。只有阶段 4/5（上传入库）需要它；采集、下载、转写、蒸馏、lint 都不依赖。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `IMA_KB_ID` | 空（**必填**） | 目标 ima 知识库 ID |
| `DOUYIN_DISTILL_ROOT` | `D:/douyin-distill`（Windows 有 D 盘）/ `~/douyin-distill` | 产物根目录：媒体、run 目录、笔记、回执 |
| `DOUYIN_WATCH_ROOT` | `DOUYIN_DISTILL_ROOT/_watch` | 发现层队列状态 |
| `WORKBUDDY_ASR_ROOT` | `DOUYIN_DISTILL_ROOT/asr` | 离线 ASR 安装目录；已有环境可直接指过去复用 |
| `DOUYIN_VENDOR_DIR` | `DOUYIN_DISTILL_ROOT/_vendor/douyin-api` | 可选的全站搜索引擎，本仓库不含 |

`SKILL.md` 和 `references/` 里的 `YOUR_IMA_KB_ID`、`YOUR_FOLDER_ID_A` 之类是**脱敏占位符**，语义上指「你的库 / 你的目录」，脚本运行时一律读 `dy_config.py`，**不需要逐个替换文件**。文档与 docstring 里残留的历史排错路径是作者本机记录，一律以 `dy_config.py` 的生效值为准。

### 边界（哪些不是纯离线脚本）

- **阶段 4 入库**依赖 WorkBuddy + ima MCP 的 OAuth 通道（token 在宿主进程，脚本不持有）。采集 → 蒸馏 → lint 可脚本化，上传与回执校验由 agent 执行。这是架构约束，不是配置问题。
- **本地 ASR**（可选）：`ffmpeg` + `sherpa-onnx` + SenseVoice 模型，用 `python scripts/setup_local_asr.py --check` 体检。
- **关键词发现**（可选）：需要外部 `zhubaohe123/Douyin-api`（MIT，不在本仓库内）。没装也能跑 `doctor.py` 和主链路——`search_discover.py` 已改为延迟加载，不会在 import 时报错。
- **网络**：下载走 `http.client` 直连，绕开注册表代理对 `urllib` TLS 的干扰；环境不同请自行调整代理配置。
- Python 3.10+（推荐 3.13）；标准库为主。

## 质量红线（摘要）

- 拿不到**真实文字来源**时必须停下并输出素材卡，**禁止**凭标题、话题或记忆臆造内容。
- 禁止把推荐流、同作者其他视频的内容当作本条视频的观点。
- 笔记保留来源链接与作者，引用保持简短并标注出处。
- 不读取、不复制、不解密浏览器登录 cookie；会话凭证默认不落盘。
- 不用于批量爬取、绕过付费/登录墙，或下载他人受版权保护内容用于再发布。

## 许可与第三方

- 本仓库整体按 **Apache-2.0** 授权（`LICENSE`）：可复用、修改、再分发、商用，需保留版权与许可证声明并注明修改。
- `scripts/dy_sign/` 与 `scripts/douyin_detail.py` 的签名实现取自上游开源项目，各自许可证与文件对应关系见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及 `references/douyin-download.md §7`。再分发请连同该 NOTICE 一起保留。
- 内容涉及第三方平台接口的分析，仅用于个人学习与内容沉淀；请勿用于批量爬取、绕过登录墙或再发布他人受版权保护的内容。

## 说明

- `references/` 里保留了大量作者本机的排错记录（磁盘容量、代理行为、Edge profile 等），是**开发笔记**而非通用前提。
- 接口签名与平台策略会变，文档记录的是 2026-11-08 的实测状态，使用前请复核。
