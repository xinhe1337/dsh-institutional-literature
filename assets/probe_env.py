# -*- coding: utf-8 -*-
"""Check whether this machine can actually reach publisher sites, and under what identity.

Run this BEFORE promising a download: the answer decides whether the whole
approach is viable (institutional IP) or whether only open-access works.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser

SITES = [
    ("ScienceDirect", "https://www.sciencedirect.com/"),
    ("Web of Science", "https://www.webofscience.com/"),
    ("Scopus", "https://www.scopus.com/"),
    ("IEEE Xplore", "https://ieeexplore.ieee.org/"),
    ("Springer", "https://link.springer.com/"),
    ("Taylor & Francis", "https://www.tandfonline.com/"),
    ("Wiley", "https://onlinelibrary.wiley.com/"),
    ("arXiv", "https://arxiv.org/"),
    ("CNKI", "https://www.cnki.net/"),
]


def html_of(ws, url, wait=12):
    browser.goto(ws, url, wait)
    return (browser.ev(ws, "document.body ? document.body.innerText : ''") or "")


def main():
    print("== network identity ==")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'} "
             "| Select-Object -ExpandProperty IPAddress"],
            capture_output=True, text=True).stdout.split()
        print("local IPs:", ", ".join(out) or "?")
    except Exception:
        pass
    for u in ("https://ipinfo.io/json",):
        try:
            import urllib.request
            d = json.loads(urllib.request.urlopen(
                urllib.request.Request(u, headers={"User-Agent": "dsh-skill/1.0"}), timeout=15).read())
            print(f"public IP: {d.get('ip')} | org: {d.get('org')} | city: {d.get('city')}")
        except Exception as e:
            print("public IP lookup failed:", e)

    print("\n== plain HTTP reachability (curl) ==")
    for name, url in SITES:
        r = subprocess.run(["curl.exe", "-sS", "-o", "NUL", "-w", "%{http_code}",
                            "--max-time", "12", "-A", "Mozilla/5.0", url],
                           capture_output=True, text=True)
        print(f"  {name:<18} {r.stdout.strip() or 'ERR'}")

    print("\n== real-browser identity (what the publisher actually sees) ==")
    browser.ensure(download_dir=os.path.join(os.getcwd(), "pdfs"))
    ws = browser.connect()
    ws.call("Page.enable")
    for name, url, marker in [("ScienceDirect", "https://www.sciencedirect.com/", "Brought to you by"),
                              ("Web of Science", "https://www.webofscience.com/", "access")]:
        text = html_of(ws, url, 14)
        hit = [ln.strip() for ln in text.splitlines() if marker.lower() in ln.lower()][:3]
        print(f"  {name}: " + (" | ".join(hit) if hit else "(no institutional marker in page text)"))
    ws.close()
    print("\nIf ScienceDirect shows an institution name, the IP is subscribed and the "
          "browser-driven download will work. If it shows a price/Get Access, only open "
          "access is available.")


if __name__ == "__main__":
    main()
