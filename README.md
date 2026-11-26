# 基于机构权限的文献下载（DSH 插件）

> **What** — A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) plugin that adds a `literature_fetch` tool and an `institutional-browser-access` skill for pulling journal PDFs.
> **Why** — Campus subscriptions are granted by egress IP, and those sites reject plain HTTP clients; so it drives a real headed Edge/Chrome on this machine instead of re-implementing the request.
> **Install** — `plugin_manager install_bundle target="<absolute path to this directory>"`, or **Plugins → Add plugin** in the desktop app. Needs Edge or Chrome, plus Python 3.

一个 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 插件：用**本机真实浏览器**走校园网/机构 IP 的订阅权限，把期刊论文的全文 PDF 取回来，并附带 Zotero 归档流程。

它同时贡献两样东西：

- **工具 `literature_fetch`**：给 DOI 清单和输出目录，插件负责起浏览器、按出版社取 PDF、回写逐条状态；
- **技能 `institutional-browser-access`**：完整方法与排错手册，含 `assets/` 下的 Python 脚本。

## 为什么需要它

高校订阅的文献库（ScienceDirect、Web of Science、IEEE Xplore、Springer…）按**出口 IP** 授权，不看你有没有登录。能不能下全文，取决于两件独立的事：

1. 这台机器的 IP 是否在机构授权范围内——决定**有没有权限**；
2. 请求是否像一个真人浏览器——决定**会不会被 Cloudflare 挡**。

`curl`、`requests`、无头浏览器通常死在第二条：它们不执行 JS、TLS 指纹不对，直接吃 403 或验证码。所以这个插件驱动的是**真实的有头浏览器**，而不是自己发 HTTP 请求。

## 安装

插件以本地目录形式安装：

```bash
# 在 DSH 里
plugin_manager install_bundle target="<本仓库的绝对路径>"
```

或者用 DSH 的图形界面：**Plugins → Add plugin → 选择本目录**。

安装后会在「插件」面板出现一条 **基于机构权限的文献下载**，并在技能列表里多出 `institutional-browser-access`。

## 使用

### 工具

```
literature_fetch(
  dois = "10.1016/j.jag.2024.103723\n10.1109/tits.2018.2864612",
  out  = "P:\\papers\\pdfs"
)
```

返回：成功数、总条数、输出目录、失败 DOI 及原因、日志尾巴。

### 技能

对模型说「用机构 IP 下这批论文」即可；或直接读 `SKILL.md` 按里面的命令行方式手工跑。

## 配置

在插件行的 `config` 里调整（`cordis.patch.yml`）：

| 键 | 默认 | 含义 |
|---|---|---|
| `pythonPath` | `python` | 解释器。留 `python` 会依次尝试 `DSH_PYTHON`、PATH 上的 `python`/`python3`、DSH 自带运行时 |
| `spacing` | `6` | 两篇之间等几秒 |
| `cooldownEvery` | `8` | 每几篇来一次长冷却（0 关闭） |
| `cooldown` | `75` | 长冷却的秒数 |
| `timeout` | `50` | 单篇等待上限（秒） |
| `skipPrefix` | `["10.1080","10.1155"]` | 自动化必败的出版社前缀，直接跳过 |

`spacing` / `cooldownEvery` / `cooldown` 也可以在**每次调用时**临时覆盖。

## 支持的出版社

| 出版社 | DOI 前缀 | 方式 | 可靠性 |
|---|---|---|---|
| Elsevier / ScienceDirect | `10.1016` | 文章页内点击 PDF 链接（应对 `crasolve` 挑战跳转） | 高 |
| IEEE Xplore | `10.1109` | `stampPDF` 直链，带限速与冷却 | 高，有频控 |
| Springer / BMC / 开放获取 | `10.1007` `10.1186` | 页面内 `.pdf` 链接，另有 OpenAlex 兜底 | 高 |
| Taylor & Francis | `10.1080` | 无稳定方案 | 低，建议手动 |
| Wiley / Hindawi | `10.1155` | 无稳定方案 | 低，建议手动 |

出版社的投递方式会变（ScienceDirect 在 2026-11 改过一次）。**批量跑之前先拿 1 个 DOI 试跑。**

## 限制与边界

- 依赖本机已安装 Edge 或 Chrome，以及 Python 3；
- 只在机构 IP 内有效，离开校园网即失效；
- **不绕过付费墙、不破解验证码、不做超量抓取**；拿不到的文献请走图书馆文献传递或联系作者；
- 请只使用你本人已经获得授权的访问权限。

## 目录

```
index.js              插件入口：注册技能与 literature_fetch 工具
SKILL.md              技能正文（方法与避坑清单）
assets/
  cdp.py              无依赖的 WebSocket/CDP 客户端
  browser.py          浏览器启动、机构身份识别、PDF 提取
  download_dois.py    CLI 批量下载器
  probe_env.py        网络与订阅身份探测
locale/{en,zh}.json   插件列表里的标题与描述
docs/DETAIL.md        插件详情页
```

## 许可

MIT
