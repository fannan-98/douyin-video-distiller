# 第三方代码与许可证声明

本仓库的签名相关实现取自 / 改编自下列开源项目。原作者的版权与许可证继续适用，本文件记录来源与文件对应关系。完整借鉴记录见 `references/douyin-download.md` §7。

| 上游项目 | 许可证 | 本仓库文件 | 复用内容 |
| --- | --- | --- | --- |
| `Evil0ctal/Douyin_TikTok_Download_API` | Apache-2.0 | `scripts/dy_sign/abogus.py` | `a_bogus` 生成、`SM3`、`browser_info` |
| 同上 | Apache-2.0 | `scripts/dy_sign/sm3.py` | SM3 纯 Python 实现 |
| 同上 | Apache-2.0 | `scripts/dy_sign/websign.py` | `x-secsdk-web-signature` 与 `SALT` |
| 同上 | Apache-2.0 | `scripts/douyin_detail.py` | `_add_web_signature` 拼接顺序、`base_params`、`AID` / `VERSION_CODE` |
| `Johnserf-Seed/TikTokDownload` | MIT | `scripts/douyin_detail.py` | `s_v_web_id` / `verifyFp` 生成算法（`Server/s_v_web_id.js`） |
| `zhubaohe123/Douyin-api` | MIT | **不在本仓库内** | 可选的全站搜索发现层（外部 vendored 依赖，见 `SKILL.md` 资源清单） |
| `xrqi/tiktok_to_knowledge` | — | 仅思路参考，无代码 | 同类「视频 → 知识库」流水线的 cookie 处理思路 |

## 改动与说明

- 本地化改动限于：相对导入改写、参数硬编码、去除异步依赖。**未改动算法**。
- `scripts/dy_sign/abogus.py` 文件头保留了上游项目的自述，其中提到「the previous revision of this module was a port of GPL-3.0 code and could not be shipped under this repository's Apache-2.0 licence」——这里的 *this repository* 指**上游仓库**，不是本仓库；上游为此独立重写了该实现，当前版本可随 Apache-2.0 分发。
- Apache-2.0 要求保留版权声明、许可证副本与修改说明（NOTICE）；MIT 要求保留版权与许可声明。**本仓库未附带上游 LICENSE 全文**，再分发时请从对应项目获取并一并附上。

## 本仓库许可证

除上表列出的第三方文件外，其余内容由本仓库作者编写，按 **Apache-2.0** 授权（见仓库根目录 `LICENSE`）。

    Copyright 2026 fannan-98

选 Apache-2.0 是为了与 `scripts/dy_sign/` 的主要上游（`Evil0ctal/Douyin_TikTok_Download_API`，Apache-2.0）保持一致；MIT 上游的版权与许可声明同样保留在上表中。再分发时请连同本文件一起保留，并注明所做的修改。
