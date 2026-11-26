# 基于机构权限的文献下载

*Fetch journal PDFs through your institution's IP with the real local browser.*

用本机真实浏览器 + 校园网（或机构）IP 取回订阅期刊的全文 PDF，全程不经过第三方下载站。

## 解决什么

高校订阅的文献库按**出口 IP** 授权，不看你有没有登录。能不能下全文取决于两件独立的事：

1. 这台机器的 IP 是否在机构授权范围内（有没有权限）；
2. 请求是否像一个真人浏览器（会不会被 Cloudflare 挡）。

`curl` 和无头浏览器通常死在第二条。所以插件驱动的是**真实的有头浏览器**，不是自己发 HTTP 请求。

## 两个入口

**工具 `literature_fetch`**：直接给 DOI 清单和输出目录，插件负责起浏览器、按出版社取 PDF、写回逐条状态。

```
literature_fetch(dois="10.1016/j.jag.2024.103723
10.1109/tits.2018.2864612", out="P:\\papers\\pdfs")
```

返回成功数、失败 DOI 与原因、输出目录和日志尾巴。

**技能 `institutional-browser-access`**：完整方法论与排错手册，含脚本用法，适合手工调试或改策略时阅读。两者共用同一套 `assets/` 脚本。

## 可调参数

插件配置（`cordis.patch.yml` 的 `config`）里有六个旋钮：

| 键 | 默认 | 含义 |
|---|---|---|
| `pythonPath` | `python` | 解释器。留 `python` 会依次尝试 `DSH_PYTHON`、PATH 上的 `python`/`python3`、DSH 自带运行时 |
| `spacing` | 6 | 两篇之间等几秒 |
| `cooldownEvery` | 8 | 每几篇来一次长冷却（0 关闭） |
| `cooldown` | 75 | 长冷却的秒数 |
| `timeout` | 50 | 单篇等待上限（秒） |
| `skipPrefix` | `10.1080`、`10.1155` | 自动化必败的出版社前缀，直接跳过 |

`spacing`、`cooldownEvery`、`cooldown` 也可以在**每次调用时**临时覆盖，不必改配置。

IEEE 有明显频控，冷却值调小会开始大面积超时；`spacing` 调到 3 秒以下也容易触发。

## 已实现的出版社

| 出版社 | DOI 前缀 | 方式 |
|---|---|---|
| Elsevier / ScienceDirect | `10.1016` | 文章页内点击 PDF 链接（应对 `crasolve` 挑战跳转） |
| IEEE Xplore | `10.1109` | `stampPDF` 直链，带限速与冷却 |
| Springer / BMC / 开放获取 | `10.1007` `10.1186` | 页面内 `.pdf` 链接，另有 OpenAlex 兜底 |

Taylor & Francis（`10.1080`）与 Wiley/Hindawi（`10.1155`）的自动化尝试会稳定失败，默认直接跳过并记入失败清单，交给用户手动下载——比反复重试把 IP 送进黑名单划算。

## 下载之后

工具只负责把 PDF 落到磁盘。归档到 Zotero 是下一步：按 DOI 建条目并放进目标分类，再把文件挂成附件。技能里写了具体调用方式（`add_by_identifier` → `get_collection_items` 取条目 key → `write_item` 导入），只在条目还没有 PDF 时补，避免重复。

## 限制

- 依赖本机已安装 Edge 或 Chrome，以及 Python 3。
- 只在机构 IP 内有效；离开校园网即失效。
- 不绕过付费墙、不破解验证码、不做超量抓取；拿不到的文献走图书馆文献传递或联系作者。
- 出版社的投递方式会变（ScienceDirect 在 2026-11 改过一次），批量跑之前应先拿 1 个 DOI 试跑。
