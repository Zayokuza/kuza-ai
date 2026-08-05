#!/usr/bin/env python3
"""Web search and webpage-reading tools for Kuza."""

import ipaddress
import json
import os
import socket
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 16) "
        "AppleWebKit/537.36 Chrome/131 Mobile Safari/537.36"
    )
}

SEARCH_URL = "https://html.duckduckgo.com/html/"
BING_SEARCH_URL = "https://www.bing.com/search"


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# Larger defaults support research tasks while preserving memory and network
# bounds on a phone. Every cap can be tuned without editing source.
MAX_RESULTS = _bounded_env_int("KUZA_WEB_RESULTS", 20, 1, 50)
MAX_PAGE_CHARS = _bounded_env_int("KUZA_WEB_PAGE_CHARS", 24000, 2000, 100000)
MAX_RESPONSE_BYTES = _bounded_env_int(
    "KUZA_WEB_RESPONSE_BYTES", 2_000_000, 100_000, 8_000_000
)
MAX_REDIRECTS = _bounded_env_int("KUZA_WEB_REDIRECTS", 8, 1, 12)


def _safe_public_url(url: str) -> bool:
    """Reject local files, localhost, and private-network addresses."""
    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.username or parsed.password:
            return False
        if parsed.port is not None and parsed.port not in {80, 443}:
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


def _read_limited_bytes(response, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read a streamed response with a hard memory bound."""
    body = bytearray()
    for chunk in response.iter_content(chunk_size=16384):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError(f"response exceeds {max_bytes} byte limit")
    return bytes(body)


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


def _parse_search_results(body: bytes, provider: str, limit: int) -> list[dict]:
    """Parse one provider response into Kuza's common result schema."""
    soup = BeautifulSoup(body.decode("utf-8", errors="replace"), "html.parser")
    results: list[dict] = []

    if provider == "duckduckgo":
        entries = soup.select(".result")
        for result in entries:
            link = result.select_one(".result__a")
            snippet = result.select_one(".result__snippet")
            if not link:
                continue
            url = _clean_ddg_url(link.get("href", ""))
            if not url.startswith(("http://", "https://")):
                continue
            results.append({
                "title": link.get_text(" ", strip=True),
                "url": url,
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
                "provider": provider,
            })
            if len(results) >= limit:
                break
    elif provider == "bing":
        for result in soup.select("li.b_algo"):
            link = result.select_one("h2 a")
            snippet = result.select_one(".b_caption p") or result.select_one("p")
            if not link:
                continue
            url = link.get("href", "")
            if not url.startswith(("http://", "https://")):
                continue
            results.append({
                "title": link.get_text(" ", strip=True),
                "url": url,
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
                "provider": provider,
            })
            if len(results) >= limit:
                break

    return results


def web_search(query: str, limit: int = 8) -> str:
    """Search multiple public providers and return structured JSON results.

    DuckDuckGo is attempted first. Bing is a fallback when the first provider
    is unavailable or returns no usable results.
    """
    query = str(query or "").strip()

    if not query:
        return "[ERROR] Search query is empty"

    try:
        limit = max(1, min(int(limit), MAX_RESULTS))
    except (TypeError, ValueError):
        limit = min(8, MAX_RESULTS)

    providers = (
        ("duckduckgo", SEARCH_URL, {"q": query}),
        ("bing", BING_SEARCH_URL, {"q": query, "count": str(limit)}),
    )
    failures: list[str] = []

    for provider, url, params in providers:
        response = None
        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=20,
                stream=True,
            )
            response.raise_for_status()
            body = _read_limited_bytes(response)
            results = _parse_search_results(body, provider, limit)
            if results:
                return json.dumps(results, indent=2, ensure_ascii=False)
            failures.append(f"{provider}: no usable results")
        except requests.RequestException as exc:
            failures.append(f"{provider}: {exc}")
        except Exception as exc:
            failures.append(f"{provider}: {exc}")
        finally:
            if response is not None:
                response.close()

    if failures:
        return "[ERROR] Web search exhausted providers: " + "; ".join(failures)
    return "No web results found."


def read_webpage(url: str, max_chars: int = MAX_PAGE_CHARS) -> str:
    """Download a public webpage and return its readable text."""
    url = str(url or "").strip()

    if not _safe_public_url(url):
        return "[ERROR] Only public HTTP/HTTPS webpages are allowed"

    try:
        max_chars = max(1000, min(int(max_chars), 30000))
    except (TypeError, ValueError):
        max_chars = MAX_PAGE_CHARS

    session = requests.Session()
    response = None
    try:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            # Validate every hop before connecting. requests' automatic redirect
            # handling would otherwise contact a private target before Kuza got
            # a chance to reject the final URL.
            if not _safe_public_url(current_url):
                return "[ERROR] Redirected to a non-public address"

            response = session.get(
                current_url,
                headers=HEADERS,
                timeout=25,
                allow_redirects=False,
                stream=True,
            )

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                response.close()
                response = None
                if not location:
                    return "[ERROR] Webpage redirect did not include a location"
                current_url = urljoin(current_url, location)
                continue
            break
        else:
            return f"[ERROR] Webpage exceeded {MAX_REDIRECTS} redirects"

        if response is None:
            return "[ERROR] Webpage request returned no response"

        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            return f"[ERROR] Unsupported content type: {content_type}"

        body = _read_limited_bytes(response)
        encoding = response.encoding or "utf-8"
        soup = BeautifulSoup(body.decode(encoding, errors="replace"), "html.parser")

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
            f"Source: {current_url}\n\n"
            f"{cleaned}"
        ).strip()

    except requests.RequestException as exc:
        return f"[ERROR] Webpage request failed: {exc}"
    except Exception as exc:
        return f"[ERROR] Webpage reading failed: {exc}"
    finally:
        if response is not None:
            response.close()
        session.close()
