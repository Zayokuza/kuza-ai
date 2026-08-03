#!/usr/bin/env python3
"""Web search and webpage-reading tools for Kuza."""

import ipaddress
import json
import socket
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 16) "
        "AppleWebKit/537.36 Chrome/131 Mobile Safari/537.36"
    )
}

SEARCH_URL = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 10
MAX_PAGE_CHARS = 12000


def _safe_public_url(url: str) -> bool:
    """Reject local files, localhost, and private-network addresses."""
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False

        hostname = parsed.hostname
        if not hostname or hostname.lower() == "localhost":
            return False

        addresses = socket.getaddrinfo(hostname, None)

        for entry in addresses:
            ip = ipaddress.ip_address(entry[4][0])

            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
            ):
                return False

        return True
    except Exception:
        return False


def _clean_ddg_url(url: str) -> str:
    """Convert DuckDuckGo redirect links into their real destination."""
    if not url:
        return ""

    if url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    if "uddg" in query:
        return unquote(query["uddg"][0])

    return url


def web_search(query: str, limit: int = 5) -> str:
    """Search DuckDuckGo and return structured JSON results."""
    query = str(query or "").strip()

    if not query:
        return "[ERROR] Search query is empty"

    try:
        limit = max(1, min(int(limit), MAX_RESULTS))
    except (TypeError, ValueError):
        limit = 5

    try:
        response = requests.get(
            SEARCH_URL,
            params={"q": query},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        results = []

        for result in soup.select(".result"):
            link = result.select_one(".result__a")
            snippet = result.select_one(".result__snippet")

            if not link:
                continue

            url = _clean_ddg_url(link.get("href", ""))

            if not url.startswith(("http://", "https://")):
                continue

            results.append(
                {
                    "title": link.get_text(" ", strip=True),
                    "url": url,
                    "snippet": (
                        snippet.get_text(" ", strip=True)
                        if snippet
                        else ""
                    ),
                }
            )

            if len(results) >= limit:
                break

        if not results:
            return "No web results found."

        return json.dumps(results, indent=2, ensure_ascii=False)

    except requests.RequestException as exc:
        return f"[ERROR] Web search failed: {exc}"
    except Exception as exc:
        return f"[ERROR] Web search failed: {exc}"


def read_webpage(url: str, max_chars: int = MAX_PAGE_CHARS) -> str:
    """Download a public webpage and return its readable text."""
    url = str(url or "").strip()

    if not _safe_public_url(url):
        return "[ERROR] Only public HTTP/HTTPS webpages are allowed"

    try:
        max_chars = max(1000, min(int(max_chars), 30000))
    except (TypeError, ValueError):
        max_chars = MAX_PAGE_CHARS

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type:
            return f"[ERROR] Unsupported content type: {content_type}"

        if not _safe_public_url(response.url):
            return "[ERROR] Redirected to a non-public address"

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(
            [
                "script",
                "style",
                "noscript",
                "svg",
                "canvas",
                "iframe",
                "nav",
                "footer",
                "form",
            ]
        ):
            tag.decompose()

        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        container = soup.find("main") or soup.find("article") or soup.body or soup
        text = container.get_text("\n", strip=True)

        lines = []
        previous = None

        for line in text.splitlines():
            line = " ".join(line.split())

            if not line or line == previous:
                continue

            previous = line
            lines.append(line)

        cleaned = "\n".join(lines)
        cleaned = cleaned[:max_chars]

        return (
            f"Title: {title}\n"
            f"Source: {response.url}\n\n"
            f"{cleaned}"
        ).strip()

    except requests.RequestException as exc:
        return f"[ERROR] Webpage request failed: {exc}"
    except Exception as exc:
        return f"[ERROR] Webpage reading failed: {exc}"
