# -*- coding: utf-8 -*-
"""Drive a real headed Edge/Chrome so the vendor's JS challenge passes.

The key insight: curl and headless browsers are rejected by Cloudflare-style
bot checks (TLS fingerprint + no JS execution). A real, headed browser on the
same machine/IP passes, and the institutional subscription is bound to that IP,
not to cookies. So we automate the browser, not the HTTP layer.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0")


def browser_path():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError("no Edge/Chrome found in the standard locations")


def kill_probe(marker):
    """Kill only the browser instances that use our throwaway profile.

    Never kill the user's own browser: match on the profile marker string.
    """
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('msedge.exe','chrome.exe') "
          f"-and $_.CommandLine -like '*{marker}*' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    time.sleep(2)


def set_pdf_pref(profile_dir, download_dir):
    """Force PDFs to be saved to disk instead of opening in the built-in viewer.

    Without this the browser renders the PDF inline and the download dir stays empty.
    """
    p = os.path.join(profile_dir, "Default", "Preferences")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = {}
    if os.path.exists(p):
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            data = {}
    data.setdefault("plugins", {})["always_open_pdf_externally"] = True
    data.setdefault("download", {})["prompt_for_download"] = False
    data["download"]["default_directory"] = download_dir
    data["download"]["directory_upgrade"] = True
    data["savefile"] = {"default_directory": download_dir}
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def launch(profile_dir=None, port=9334, download_dir=None, url="about:blank"):
    exe = browser_path()
    profile_dir = profile_dir or os.path.join(tempfile.gettempdir(), "dsh_lit_profile")
    if download_dir:
        set_pdf_pref(profile_dir, download_dir)
    subprocess.Popen([exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile_dir}",
                      "--no-first-run", "--no-default-browser-check",
                      "--window-size=1280,900", "--disable-quic", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp.wait_port(port, timeout=60)
    return profile_dir


def start_fresh(profile_dir=None, port=9334, download_dir=None, url="about:blank"):
    """Kill our own probe instance, rewrite the PDF/download prefs, then launch.

    Reusing a live instance is unreliable: the browser rewrites Preferences on
    startup and can flip `prompt_for_download` back to true, which makes every
    download wait on a Save-As dialog and produces no file at all.
    """
    profile_dir = profile_dir or os.path.join(tempfile.gettempdir(), "dsh_lit_profile")
    kill_probe(os.path.basename(profile_dir))
    if download_dir:
        set_pdf_pref(profile_dir, download_dir)
    return launch(profile_dir, port, download_dir, url)


def ensure(profile_dir=None, port=9334, download_dir=None):
    """Reuse a running probe browser if present, otherwise start one."""
    try:
        cdp.wait_port(port, timeout=2)
        return profile_dir or os.path.join(tempfile.gettempdir(), "dsh_lit_profile")
    except TimeoutError:
        return launch(profile_dir, port, download_dir)


def connect(port=9334):
    for t in cdp.targets(port):
        if t.get("type") == "page":
            return cdp.WS(t["webSocketDebuggerUrl"])
    raise RuntimeError("no page target")


def ev(ws, expr, timeout=25):
    r = ws.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                     "awaitPromise": False}, timeout=timeout)
    return r["result"].get("value")


def goto(ws, url, wait=7, challenge_wait=12):
    """Navigate and give a JS challenge a second chance to clear."""
    try:
        ws.call("Page.navigate", {"url": url}, timeout=30)
    except Exception:
        pass
    time.sleep(wait)
    title = ev(ws, "document.title") or ""
    if any(k in title for k in ("请稍候", "Just a moment", "安全验证", "Are you a robot")):
        time.sleep(challenge_wait)
        title = ev(ws, "document.title") or ""
    return title


def pii_of(url):
    m = re.search(r"/pii/([A-Z0-9]+)", url or "")
    return m.group(1) if m else None


def arnumber_of(url):
    m = re.search(r"arnumber=(\d+)", url or "") or re.search(r"/document/(\d+)", url or "")
    return m.group(1) if m else None


def wait_for_selector(ws, js_expr, timeout=15, gap=1.5):
    """Poll a JS expression until it returns a truthy value (e.g. the PDF anchor)."""
    end = time.time() + timeout
    val = None
    while time.time() < end:
        val = ev(ws, js_expr)
        if val:
            return val
        time.sleep(gap)
    return val


def grab_pdf(ws, doi, download_dir, before, timeout=50):
    """Find the PDF entry point on the current article page and pull it down.

    Navigation vs click matters: ScienceDirect now inserts a `pdfft?crasolve=1`
    challenge redirect that only clears when the link is clicked from inside the
    article page (it needs the page's referer and JS context). Navigating to the
    same URL directly lands on a blank interstitial and hangs. IEEE, by contrast,
    is more reliable as a direct stampPDF navigation.

    Returns (path or None, strategy).
    """
    doi = (doi or "").lower()

    # doi.org may stop on Elsevier's linkinghub redirect page; normalise it.
    page_url = ev(ws, "location.href") or ""
    if "linkinghub.elsevier.com" in page_url:
        pii = pii_of(page_url)
        if pii:
            goto(ws, f"https://www.sciencedirect.com/science/article/pii/{pii}", 10)
            page_url = ev(ws, "location.href") or ""

    if "10.1016/" in doi:
        href = wait_for_selector(
            ws, """(()=>{const a=document.querySelector('a[href*="pdfft"]');return a?a.href:''})()""",
            timeout=18)
        if not href:
            return None, "elsevier-no-link"
        ev(ws, """(()=>{const a=document.querySelector('a[href*="pdfft"]');
                 if(a){a.target='_self';a.click();return 1}return 0})()""")
        return wait_download(download_dir, before, timeout=timeout), "elsevier-click"

    if "10.1109/" in doi:
        n = arnumber_of(page_url)
        if n:
            goto(ws, f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={n}&ref=", 3)
            return wait_download(download_dir, before, timeout=timeout), "ieee-stamp"

    href = ev(ws, r"""(()=>{
        const as=[...document.querySelectorAll('a')];
        const hit=as.find(a=>a.href&&/\.pdf(\?|$)/i.test(a.href))
              || as.find(a=>a.href&&/pdf/i.test(a.href))
              || as.find(a=>/download pdf|full text pdf|view pdf/i.test(a.innerText||''));
        if(!hit) return '';
        hit.target='_self'; hit.click(); return hit.href;
    })()""")
    if href:
        return wait_download(download_dir, before, timeout=timeout), "generic-click"
    return None, "no-entry"


def wait_download(download_dir, before, timeout=50, min_bytes=2048):
    """Wait for a new, non-partial file that actually starts with %PDF."""
    end = time.time() + timeout
    last, stable = -1, 0
    while time.time() < end:
        time.sleep(1.5)
        new = [f for f in os.listdir(download_dir) if f not in before]
        done = [f for f in new if not f.endswith(".crdownload")]
        if done:
            p = os.path.join(download_dir, done[0])
            size = os.path.getsize(p)
            if size == last:
                stable += 1
                if stable >= 2:
                    if size < min_bytes or open(p, "rb").read(4) != b"%PDF":
                        return None
                    return p
            else:
                last, stable = size, 0
    return None


def openalex_oa(doi):
    """Open-access PDF candidate from OpenAlex, used as a fallback."""
    import urllib.request
    u = f"https://api.openalex.org/works/https://doi.org/{doi}?mailto=dsh-lit@example.com"
    for _ in range(3):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "dsh-skill/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=25).read())
            for L in [d.get("best_oa_location") or {}] + (d.get("locations") or []):
                for k in ("pdf_url", "landing_page_url"):
                    v = L.get(k) or ""
                    if v and (".pdf" in v.lower() or "arxiv" in v.lower()):
                        return v
            return (d.get("open_access") or {}).get("oa_url") or ""
        except Exception:
            time.sleep(3)
    return ""


def curl_pdf(url, dest, timeout=90):
    r = subprocess.run(["curl.exe", "-sSL", "--max-time", str(timeout), "-A", UA_CHROME,
                        "-o", dest, url], capture_output=True, text=True)
    if os.path.exists(dest) and open(dest, "rb").read(4) == b"%PDF":
        return True
    if os.path.exists(dest):
        os.remove(dest)
    return False
