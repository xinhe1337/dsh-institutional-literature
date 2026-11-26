---
name: institutional-browser-access
description: 用本机真实浏览器（Edge/Chrome）借校园网或机构 IP 的订阅权限检索、下载期刊论文全文 PDF，并把 PDF 归入 Zotero。当用户说"用我的电脑和 IP 去查/下论文"、需要批量获取付费墙后的原文 PDF、或 curl / 无头浏览器被 Cloudflare 之类反爬挡住时加载本技能。
---

# 借本机浏览器与机构 IP 获取文献全文

## 这套方法解决什么

很多高校订阅的文献库（ScienceDirect、Web of Science、IEEE Xplore、Springer 等）**按出口 IP 授权**，不看你有没有登录。所以"能不能下全文"，取决于两个独立条件：

1. **IP 是否在机构授权范围内**——这决定有没有权限；
2. **请求是否像一个真人浏览器**——这决定会不会被 Cloudflare 挡在门外。

`curl`、`requests`、无头浏览器通常挂在第 2 条：它们不执行 JS、TLS 指纹不对，直接吃 403 或验证码。而用户自己的 Edge 能进，不是因为它特殊，是因为它同时满足这两条。

**因此本技能的核心动作是：驱动那台真实浏览器，而不是自己发 HTTP 请求。**

## 开始前必须确认（否则会白干）

1. **网络身份**：先跑 `assets/probe_env.py`。如果 ScienceDirect 页面出现 "Brought to you by: <你的学校>"，说明 IP 有订阅；如果只有 "Get Access / Purchase PDF"，那就只有开放获取能下，后面的付费下载逻辑不成立。
2. **授权边界**：只用用户本人已有的访问权限，不绕付费墙、不碰验证码破解、不做超量抓取。用户拿不到的文献，老老实实给 DOI 让他走图书馆文献传递。
3. **不要干扰用户正在用的浏览器**：始终用独立临时 profile 启动一个探针实例，退出时按 profile 标记精确回收进程，绝不 `taskkill /IM msedge.exe`。

## 两个入口：工具优先

插件同时注册了工具 `literature_fetch(dois, out)`。**批量下载优先用它**——不必自己拼命令行、不必解析脚本输出，它会返回成功数、失败 DOI 与原因、输出目录。

```
literature_fetch(dois="10.1016/j.jag.2024.103723
10.1109/tits.2018.2864612", out="P:\\硕士大论文\\pdfs")
```

只有在需要改策略、调脚本、或排查某个出版社为何失败时，才直接跑下面的脚本。

## 环境探测

```bash
python assets/probe_env.py
```

输出本机 IP、公网出口 IP 与归属、各出版社的 HTTP 可达性，以及真实浏览器里看到的机构名。据此判断后续走"订阅下载"还是"只走开放获取"。

## 批量下载

准备 DOI 清单（每行一个，或 JSON），然后：

```bash
python assets/download_dois.py dois.txt --out pdfs \
    --spacing 6 --cooldown-every 8 --cooldown 75 \
    --skip-prefix 10.1080 10.1155
```

- 结果写入 `pdfs/status.json`，逐条记录 `ok`、`file`、失败原因；
- 重复运行会自动跳过已成功的条目（可断点续跑）；
- 默认按 DOI 解析到出版社，再按出版社规则找 PDF 直链。

## 关键机制（改脚本前先看这段）

| 机制 | 为什么必须这样 |
|---|---|
| 有头浏览器 + 独立 profile | 无头模式会被识别；用用户主 profile 会被文件锁挡住 |
| 每次跑批都**重新启动**实例（`start_fresh`） | 复用已开的实例时，浏览器会把 `prompt_for_download` 改回 true，每篇都弹"另存为"对话框，结果一个文件都没有 |
| `plugins.always_open_pdf_externally = true` | 否则 PDF 在内置阅读器里打开，下载目录永远是空的 |
| 下载目录必须是**绝对路径** | CDP 的 `downloadPath` 不解析相对路径，文件会落到浏览器自己的工作目录，脚本这边看起来"没下到" |
| Elsevier 要"点"链接，不能直接跳转 | ScienceDirect 现在会跳到 `pdfft?crasolve=1` 的挑战中间页，直接导航会停在空白页；在文章页里 `a.click()` 才带得上 referer 与 JS 上下文 |
| IEEE 用 `stampPDF` 直链跳转 | 反过来，IEEE 直链比点击稳，点击常开新标签页 |
| 下载完成判定要校验 `%PDF` 文件头 | 失败时也会生成 0 字节或 `.crdownload`，不校验就会把 HTML 错误页当论文 |
| 出版社限速（IEEE 尤其） | 连续 12 篇左右开始超时；间隔 6 秒 + 每 8 篇冷却 75 秒可稳定跑完全量 |

## 出版社差异（实测，2026-11）

| 出版社 | DOI 前缀 | 取 PDF 的方式 | 可靠性 |
|---|---|---|---|
| Elsevier / ScienceDirect | `10.1016` | 文章页里点 `a[href*="pdfft"]`（勿直接导航） | 高 |
| IEEE Xplore | `10.1109` | `stampPDF/getPDF.jsp?tp=&arnumber=<N>&ref=`，N 从落地页 URL 取 | 高，但**有频控** |
| Springer / BMC / OA | `10.1007` `10.1186` | 页面里的 `.pdf` 链接 | 高 |
| Taylor & Francis | `10.1080` | 无稳定方案 | **基本必败**，让用户手动下 |
| Wiley / Hindawi | `10.1155` | 无稳定方案 | **基本必败**，让用户手动下 |

出版社的投递方式会变（ScienceDirect 在 2026-11 就改过一次）。**跑批之前先拿 1 个 DOI 试跑**，确认策略仍然有效，再放开全量。

失败不要硬试：直接给用户 `https://doi.org/<DOI>`，让他自己在浏览器点一下，通常 10 秒搞定，比脚本反复重试快得多。

## 开放获取兜底

订阅失败时先查有没有合法 OA 副本（OpenAlex 的 `best_oa_location` / `oa_url`），能下的直接 `curl`（arXiv、机构库、PMC 都通）。三处都判 `closed` 才是真的没有：OpenAlex、Unpaywall、Semantic Scholar。

## 归入 Zotero

下载完成后用 Zotero 的 MCP 工具，不要手工建条目：

1. 先按 DOI 建条目并放进目标分类：
   `add_by_identifier(identifiers=[...], collectionKey=..., saveAttachments=true)`
2. 用 `get_collection_items` 拿到 **DOI → itemKey** 映射（输出很大时读它的 spill 文件，用正则抽 `"key"` 与 `"DOI"`）。
3. 只在条目**还没有 PDF** 时补附件，避免重复：
   `write_item(action="import", filePath=..., parentItemKey=..., title="Publisher PDF")`
4. 缺摘要的条目可以顺手补：`write_metadata(itemKey=..., fields={"abstractNote": ...})`。

## 收尾检查

- [ ] `status.json` 里每条 `ok` 的 `file` 都实际存在且以 `%PDF` 开头；
- [ ] Zotero 分类里的条目数与清单一致，没有重复条目；
- [ ] 失败条目已整理成"DOI + 期刊 + 原因"清单交给用户；
- [ ] 探针浏览器已关闭（按 profile 标记回收），用户的浏览器未受影响；
- [ ] 若下载文件体积异常（例如单篇 100 MB），提醒用户 Zotero 云同步配额（免费 300 MB）。

## 反面清单

- ❌ 用 `curl` 直接打 ScienceDirect / IEEE 的文章页，然后断定"没有权限"——那是反爬，不是权限。
- ❌ 用用户主 profile 启动第二个浏览器实例——会被锁，或抢走用户的会话。
- ❌ 复用已开着的探针实例跑批——下载偏好被浏览器改写，会静默卡在另存为对话框。
- ❌ 给 `--out` 传相对路径——文件会下到别处，脚本误报失败。
- ❌ 把 `.crdownload`、0 字节文件、HTML 错误页当成下载成功。
- ❌ 对 `10.1080` / `10.1155` 死磕；重试只会让 IP 进临时黑名单，连用户手动都打不开。
- ❌ 把 cookie 导出后拿去做批量请求——TLS 指纹不匹配，照样 403。
