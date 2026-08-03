#!/usr/bin/env python3

import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

UA = {
    "User-Agent": "Mozilla/5.0"
}

def search(query, limit=5):
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    r = requests.get(url, headers=UA, timeout=20)

    soup = BeautifulSoup(r.text, "html.parser")

    results = []

    for a in soup.select(".result__a")[:limit]:
        results.append({
            "title": a.get_text(" ", strip=True),
            "url": a.get("href")
        })

    return results


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:])

    for r in search(q):
        print(f"{r['title']}\n{r['url']}\n")
