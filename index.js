import { readFile, writeFile, mkdtemp, rm, access } from "node:fs/promises";
import { tmpdir, homedir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

/**
 * Institutional-access literature download.
 *
 * Two contributions:
 *  - a skill (`institutional-browser-access`) that explains the method and the
 *    pitfalls, with the Python helpers shipped in `assets/`;
 *  - a `literature_fetch` tool that runs those helpers without a shell round-trip.
 *
 * The plugin deliberately imports nothing outside `node:` builtins: a bundle
 * installed from a workspace directory is resolved by real path, so host
 * packages such as `@deepseek-ai/dsh-tools` would not be reachable.
 *
 * @module @local/institutional-browser-access
 */

const PROVIDER_NAME = "institutional-browser-access";
const SKILL_BODY_URL = new URL("./SKILL.md", import.meta.url);
const DOWNLOAD_SCRIPT = fileURLToPath(new URL("./assets/download_dois.py", import.meta.url));
const RESOURCE_BASE = {
  kind: "directory",
  path: fileURLToPath(new URL("./", import.meta.url)),
};
/** Standard precedence rank for packaged skill providers. */
const BUNDLED_SKILL_RANK = 600;

const CANDIDATE = {
  name: PROVIDER_NAME,
  description:
    "用本机真实浏览器（Edge/Chrome）借校园网或机构 IP 的订阅权限检索、下载期刊论文全文 PDF，并把 PDF 归入 Zotero。"
    + "当用户说“用我的电脑和 IP 去查/下论文”、需要批量获取付费墙后的原文 PDF、"
    + "或 curl / 无头浏览器被 Cloudflare 之类反爬挡住时加载本技能。",
  invocation: {
    modelInvocable: true,
    userInvocable: true,
  },
  provider: PROVIDER_NAME,
  source: "bundled",
  resourceBase: RESOURCE_BASE,
  rank: BUNDLED_SKILL_RANK,
  locator: SKILL_BODY_URL,
};

const provider = {
  name: PROVIDER_NAME,
  list: () => Promise.resolve([CANDIDATE]),
  async get() {
    return {
      name: CANDIDATE.name,
      description: CANDIDATE.description,
      invocation: CANDIDATE.invocation,
      provider: CANDIDATE.provider,
      source: CANDIDATE.source,
      resourceBase: RESOURCE_BASE,
      content: await readFile(SKILL_BODY_URL, "utf8"),
    };
  },
};

/** Defaults for the rate limits; override them in the row's `config`. */
const DEFAULTS = {
  pythonPath: "python",
  spacing: 6,
  cooldownEvery: 8,
  cooldown: 75,
  timeout: 50,
  skipPrefix: ["10.1080", "10.1155"],
};

/** Probe one interpreter: does it start and speak Python 3? */
function probePython(candidate, timeoutMs = 8000) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(candidate, ["-c", "import sys;sys.exit(0 if sys.version_info[0]==3 else 1)"], {
        windowsHide: true,
      });
    } catch {
      resolve(false);
      return;
    }
    const timer = setTimeout(() => {
      try { child.kill(); } catch { /* already gone */ }
      resolve(false);
    }, timeoutMs);
    child.on("error", () => { clearTimeout(timer); resolve(false); });
    child.on("close", (code) => { clearTimeout(timer); resolve(code === 0); });
  });
}

/**
 * Resolve the interpreter to run the packaged scripts.
 *
 * `pythonPath` wins when it is not the plain default; otherwise the candidates
 * are probed in order, so a DSH-managed runtime works without hard-coding a
 * user-specific absolute path into the package.
 */
async function resolvePython(configured) {
  const wanted = String(configured ?? "").trim();
  if (wanted && wanted !== "python") return wanted;
  const candidates = [
    process.env.DSH_PYTHON,
    "python",
    "python3",
    join(homedir(), ".dsh", "dsh-runtimes", "dsh-primary-runtime", "dependencies", "python", "python.exe"),
    "/usr/bin/python3",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (candidate.includes("/") || candidate.includes("\\")) {
      try {
        await access(candidate);
      } catch {
        continue;
      }
    }
    if (await probePython(candidate)) return candidate;
  }
  return wanted || "python";
}

const INPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    dois: {
      type: "string",
      description: "One DOI per line or comma-separated, e.g. 10.1016/j.jag.2024.103723.",
    },
    out: {
      type: "string",
      description: "Absolute directory that receives the PDFs and status.json.",
    },
    spacing: {
      type: "number",
      description: "Seconds between papers. Omit to use the plugin setting.",
    },
    cooldownEvery: {
      type: "number",
      description: "Pause after this many papers (0 disables). Omit to use the plugin setting.",
    },
    cooldown: {
      type: "number",
      description: "Longer pause length in seconds. Omit to use the plugin setting.",
    },
  },
  required: ["dois", "out"],
};

const OUTPUT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    ok: { type: "number", description: "Papers downloaded successfully." },
    total: { type: "number", description: "DOIs attempted." },
    out: { type: "string", description: "Output directory." },
    failed: {
      type: "array",
      description: "DOIs that produced no PDF, with the recorded reason.",
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          doi: { type: "string" },
          reason: { type: "string" },
        },
        required: ["doi", "reason"],
      },
    },
    log: { type: "string", description: "Tail of the downloader output." },
  },
  required: ["ok", "total", "out", "failed", "log"],
};

/** Split a DOI blob into bare identifiers, dropping duplicates. */
function splitDois(raw) {
  const out = [];
  const seen = new Set();
  for (const token of String(raw ?? "").split(/[\s,;]+/)) {
    const m = token.match(/10\.\d{4,9}\/[^\s,;"']+/);
    if (!m) continue;
    const doi = m[0].replace(/[.。]+$/, "").toLowerCase();
    if (seen.has(doi)) continue;
    seen.add(doi);
    out.push(doi);
  }
  return out;
}

/** Run the packaged downloader and collect its exit status. */
function runDownloader(python, args, signal) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, args, { windowsHide: true, signal });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (err) => {
      if (err && err.code === "ENOENT") {
        reject(new Error(
          `找不到 Python 解释器 “${python}”。请在插件配置里把 pythonPath 指向一个 Python 3，`
          + "或把它加入 PATH。",
        ));
        return;
      }
      reject(err);
    });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

/**
 * Register the skill provider and the `literature_fetch` tool.
 * @param ctx - plugin context carrying the skills and tools registries.
 * @param config - resolved plugin settings (row `config`).
 */
export function apply(ctx, config) {
  const settings = { ...DEFAULTS, ...(config ?? {}) };
  const skipPrefix = Array.isArray(settings.skipPrefix) ? settings.skipPrefix : DEFAULTS.skipPrefix;

  ctx.skills.registerProvider(() => provider);

  ctx.tools.register({
    name: "literature_fetch",
    description:
      "Download journal article PDFs through this machine's institutional IP using the real local browser. "
      + "Works for Elsevier/ScienceDirect and IEEE Xplore when the campus subscription covers them; "
      + "falls back to open-access copies; reports a per-DOI status so failures can be handed to the user. "
      + "Use when the user asks to fetch paywalled papers rather than to search for them.",
    parameters: INPUT_SCHEMA,
    output: {
      schema: OUTPUT_SCHEMA,
      render: (_args, value) => [{
        type: "text",
        text: `${value.ok}/${value.total} 篇下载成功 → ${value.out}`
          + (value.failed.length
            ? `\n失败：${value.failed.map((f) => `${f.doi}（${f.reason}）`).join("；")}`
            : ""),
      }],
    },
    async execute(args, exec) {
      const dois = splitDois(args?.dois);
      if (dois.length === 0) {
        throw new Error("没有解析出任何 DOI，请检查输入格式（例如 10.1016/j.jag.2024.103723）。");
      }
      const out = String(args?.out ?? "").trim();
      if (!out) throw new Error("out 必须是 PDF 的输出目录。");

      const python = await resolvePython(settings.pythonPath);
      const workdir = await mkdtemp(join(tmpdir(), "literature-fetch-"));
      const listFile = join(workdir, "dois.txt");
      try {
        await writeFile(listFile, dois.join("\n"), "utf8");
        const cli = [
          DOWNLOAD_SCRIPT,
          listFile,
          "--out", out,
          "--spacing", String(args?.spacing ?? settings.spacing),
          "--cooldown-every", String(args?.cooldownEvery ?? settings.cooldownEvery),
          "--cooldown", String(args?.cooldown ?? settings.cooldown),
          "--timeout", String(settings.timeout),
        ];
        if (skipPrefix.length) cli.push("--skip-prefix", ...skipPrefix);

        const { code, stdout, stderr } = await runDownloader(python, cli, exec?.signal);

        const statusFile = join(out, "status.json");
        let status = {};
        try {
          status = JSON.parse(await readFile(statusFile, "utf8"));
        } catch {
          status = {};
        }
        const entries = Object.entries(status);
        const tail = (stdout + "\n" + stderr).trim().split("\n").slice(-12).join("\n");
        if (entries.length === 0) {
          throw new Error(`下载器没有产出 status.json（exit ${code}）：\n${tail || "(无输出)"}`);
        }
        const failed = entries
          .filter(([, v]) => !v?.ok)
          .map(([doi, v]) => ({ doi, reason: String(v?.reason ?? "unknown").slice(0, 160) }));
        const ok = entries.filter(([, v]) => v?.ok).length;
        return { ok, total: dois.length, out, failed, log: tail };
      } finally {
        await rm(workdir, { recursive: true, force: true });
      }
    },
  });
}

/** Cordis plugin name. */
export const name = "institutional-browser-access";
/** Services this plugin contributes to. */
export const inject = ["skills", "tools"];


