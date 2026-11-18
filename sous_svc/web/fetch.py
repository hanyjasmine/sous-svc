import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SousRecipeBot/0.1)",
    "Accept": "text/html,application/xhtml+xml",
}


class FetchError(Exception):
    pass


# urls come from search results, so never let one reach a private network
def is_public_url(url: str) -> bool:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except (socket.gaierror, UnicodeError):
        return False
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def fetch_html(url: str, http: httpx.Client) -> str:
    # follow redirects by hand so every hop gets the same check
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(url):
            raise FetchError(f"blocked url {url}")
        with http.stream("GET", url, headers=HEADERS, follow_redirects=False) as res:
            if res.is_redirect:
                url = urljoin(url, res.headers.get("location", ""))
                continue
            if res.status_code != 200:
                raise FetchError(f"{url} returned {res.status_code}")
            if "html" not in res.headers.get("content-type", ""):
                raise FetchError(f"{url} is not html")
            chunks, size = [], 0
            for chunk in res.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FetchError(f"{url} is too large")
                chunks.append(chunk)
            return b"".join(chunks).decode(res.encoding or "utf-8", errors="replace")
    raise FetchError(f"too many redirects for {url}")
