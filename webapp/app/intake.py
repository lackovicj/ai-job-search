"""Posting intake: fetch a URL safely, run the integrity gate, classify the source host.

Mirrors /apply Step 0 and its Source Host Verification (.claude/commands/apply.md),
using the repo's own tools/posting_guard.py, tools/robots_check.py and
tools/fetch_page.py rather than re-implementing them.
"""

import ipaddress
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import fetch_page
import posting_guard
import robots_check

PORTAL_HOSTS = ["jobindex.dk", "linkedin.com", "jobnet.dk", "jobbank.dk",
                "jobdanmark.dk", "freehire.me"]
ATS_APEXES = ["greenhouse.io", "lever.co", "myworkdayjobs.com", "workday.com",
              "ashbyhq.com", "smartrecruiters.com", "workable.com"]


class IntakeError(Exception):
    pass


def _matches(host: str, apex: str) -> bool:
    return host == apex or host.endswith("." + apex)


def classify_host(url: str) -> str:
    """Exact apex or true subdomain only; look-alikes fail closed (apply.md #431)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password:
        return f"unverified: {host or url}"
    if any(_matches(host, h) for h in PORTAL_HOSTS):
        return f"portal board: {host}"
    if any(_matches(host, a) for a in ATS_APEXES):
        return f"official ATS: {host}"
    return f"unverified: {host}"


def _assert_public(url: str) -> None:
    """Refuse URLs that resolve to private or loopback space: the server sits inside
    the Coolify network and must not become a proxy into it."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise IntakeError("Only http(s) URLs are supported.")
    if parts.username or parts.password:
        raise IntakeError("URLs with embedded credentials are refused.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None)
    except socket.gaierror as exc:
        raise IntakeError(f"Could not resolve {parts.hostname}: {exc}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise IntakeError(f"{parts.hostname} resolves to a non-public address; refused.")


def fetch_url(url: str) -> str:
    _assert_public(url)
    code, message = robots_check.gate(url)
    if code != 0:
        raise IntakeError(f"robots.txt check: {message}. Paste the posting text instead.")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page.html"
        try:
            status = fetch_page.fetch_with_urllib(url, out)
        except Exception as exc:  # network, size cap, redirects
            raise IntakeError(f"Fetch failed: {exc}. Paste the posting text instead.") from exc
        if not status.startswith("HTTP 2"):
            raise IntakeError(f"Fetch returned {status}. Paste the posting text instead.")
        return out.read_text(encoding="utf-8", errors="replace")


def take_posting(url: str, text: str) -> dict:
    """Returns cleaned posting text, gate findings and host classification."""
    url, text = url.strip(), text.strip()
    if text:
        cleaned, findings = posting_guard.guard(text, "auto")
    elif url:
        cleaned, findings = posting_guard.guard(fetch_url(url), "html")
    else:
        raise IntakeError("Paste the posting text or give a URL.")
    if len(cleaned.strip()) < 200:
        raise IntakeError("The posting text is too short to evaluate - the page may be a "
                          "login wall or rendered by JavaScript. Paste the text instead.")
    return {
        "posting": cleaned,
        "guard_verdict": findings.get("verdict", ""),
        "guard_findings": findings,
        "host_class": classify_host(url) if url else "pasted text",
        "source_url": url,
    }
