# -*- coding: utf-8 -*-
"""Batch-download journal PDFs through the institution's IP, via a real browser.

Usage:
  python download_dois.py dois.txt --out pdfs [--resume status.json]
                                   [--port 9334] [--spacing 6]
                                   [--cooldown-every 8 --cooldown 75]
                                   [--skip-prefix 10.1080 10.1155]

Input file: one DOI per line ("10.1016/j.jag.2024.103723") or a JSON object
{"<doi>": {...}} / list of DOIs.

Output: <out>/<safe-doi>.pdf plus status.json recording ok / reason per DOI.

Exit: 0 always; read status.json for results. Prints a one-line summary.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser


def load_dois(path):
    raw = open(path, encoding="utf-8").read().strip()
    if raw.startswith("{") or raw.startswith("["):
        data = json.loads(raw)
        return list(data.keys()) if isinstance(data, dict) else list(data)
    out = []
    for line in raw.splitlines():
        line = line.strip().strip(",")
        if not line or line.startswith("#"):
            continue
        m = re.search(r"10\.\d{4,9}/[^\s,;\"']+", line)
        if m:
            out.append(m.group(0).rstrip("."))
    return out


def safe_name(doi):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", doi) + ".pdf"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dois_file")
    ap.add_argument("--out", default="pdfs")
    ap.add_argument("--status", default=None, help="status json path (default <out>/status.json)")
    ap.add_argument("--port", type=int, default=9334)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--spacing", type=float, default=5.0, help="seconds between papers")
    ap.add_argument("--cooldown-every", type=int, default=8, help="pause after N papers (0=off)")
    ap.add_argument("--cooldown", type=float, default=75.0, help="pause length in seconds")
    ap.add_argument("--skip-prefix", nargs="*", default=[],
                    help="publisher prefixes known to be Cloudflare-blocked, e.g. 10.1080 10.1155")
    ap.add_argument("--timeout", type=float, default=50.0)
    args = ap.parse_args()

    args.out = os.path.abspath(args.out)  # CDP downloadPath must be absolute
    os.makedirs(args.out, exist_ok=True)
    status_path = args.status or os.path.join(args.out, "status.json")
    status = json.load(open(status_path, encoding="utf-8")) if os.path.exists(status_path) else {}

    dois = load_dois(args.dois_file)
    browser.start_fresh(args.profile, args.port, args.out)
    ws = browser.connect(args.port)
    ws.call("Page.enable")
    ws.call("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": args.out})
    # warm up so the challenge cookie is issued for this profile
    browser.goto(ws, "https://www.sciencedirect.com/", 6)

    done = 0
    for i, doi in enumerate(dois, 1):
        target = os.path.join(args.out, safe_name(doi))
        if status.get(doi, {}).get("ok") and os.path.exists(target):
            continue
        if any(doi.lower().startswith(p.lower()) for p in args.skip_prefix):
            status[doi] = {"ok": False, "reason": "skipped: known Cloudflare-blocked publisher"}
            print(f"[{i}/{len(dois)}] SKIP {doi}", flush=True)
            continue

        before = set(os.listdir(args.out))
        try:
            title = browser.goto(ws, "https://doi.org/" + doi, 8)
            page_url = browser.ev(ws, "location.href") or ""
            if "chrome-error" in page_url:
                time.sleep(5)
                title = browser.goto(ws, "https://doi.org/" + doi, 10)
                page_url = browser.ev(ws, "location.href") or ""
            if any(k in title for k in ("请稍候", "Just a moment", "Are you a robot", "安全验证")):
                status[doi] = {"ok": False, "reason": "bot challenge", "page": title[:80]}
                print(f"[{i}/{len(dois)}] FAIL {doi} challenge", flush=True)
            else:
                p, strategy = browser.grab_pdf(ws, doi, args.out, before, timeout=args.timeout)
                if p:
                    os.replace(p, target)
                    status[doi] = {"ok": True, "file": target, "strategy": strategy,
                                   "size": os.path.getsize(target)}
                    print(f"[{i}/{len(dois)}] OK   {doi} {os.path.getsize(target)//1024}KB {strategy}",
                          flush=True)
                else:
                    oa = browser.openalex_oa(doi)
                    alt = target.replace(".pdf", "__oa.pdf")
                    if oa and browser.curl_pdf(oa, alt):
                        status[doi] = {"ok": True, "file": alt, "strategy": "oa-fallback", "oa_url": oa}
                        print(f"[{i}/{len(dois)}] OK   {doi} {os.path.getsize(alt)//1024}KB oa",
                              flush=True)
                    else:
                        status[doi] = {"ok": False, "reason": "no pdf / timeout",
                                       "strategy": strategy, "page_url": page_url[:160],
                                       "oa_url": oa}
                        print(f"[{i}/{len(dois)}] FAIL {doi} {strategy}", flush=True)
        except Exception as e:
            status[doi] = {"ok": False, "reason": f"error: {e}"[:180]}
            print(f"[{i}/{len(dois)}] FAIL {doi} err", flush=True)

        json.dump(status, open(status_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        done += 1
        time.sleep(args.spacing)
        if args.cooldown_every and done % args.cooldown_every == 0:
            print(f"cooldown {args.cooldown:.0f}s", flush=True)
            time.sleep(args.cooldown)

    ok = sum(1 for v in status.values() if v.get("ok"))
    print(f"DONE ok={ok} total={len(dois)} status={status_path}", flush=True)
    ws.close()


if __name__ == "__main__":
    main()


