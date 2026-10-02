"""Opt-in bounded crawling with exact host approval and pinned public addresses."""

from __future__ import annotations

import ipaddress
import socket
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]
from pydantic import BaseModel, Field


class CrawlError(ValueError):
    pass


class CrawlRequest(BaseModel):
    url: str = Field(max_length=2048)
    max_pages: int = Field(default=10, ge=1, le=50)
    max_depth: int = Field(default=1, ge=0, le=3)
    max_bytes: int = Field(default=2_000_000, ge=1, le=10_000_000)
    sitemap: bool = False


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise CrawlError("Only HTTP and HTTPS URLs are allowed")
    if parts.username or parts.password or parts.port not in {None, 80, 443}:
        raise CrawlError("Credentials and nonstandard ports are forbidden")
    host = parts.hostname.lower().encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit(
        (parts.scheme.lower(), netloc, parts.path or "/", parts.query, "")
    )


def destination(url: str, approved_hosts: list[str]) -> tuple[str, str]:
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    host = parts.hostname or ""
    if host not in approved_hosts:
        raise CrawlError("Destination host is not approved")
    try:
        addresses = {
            str(item[4][0])
            for item in socket.getaddrinfo(
                host,
                parts.port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except OSError as error:
        raise CrawlError("Destination DNS resolution failed") from error
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise CrawlError("Destination resolves to a forbidden address")
    address = sorted(addresses)[0]
    authority = f"[{address}]" if ":" in address else address
    if parts.port:
        authority += f":{parts.port}"
    return normalized, urlunsplit(
        (parts.scheme, authority, parts.path, parts.query, "")
    )


@dataclass(frozen=True)
class Page:
    url: str
    content: bytes
    media_type: str


def fetch(
    url: str,
    approved_hosts: list[str],
    byte_limit: int,
    *,
    redirect_policy: Callable[[str], bool] | None = None,
) -> Page:
    """Pin the connection to vetted DNS, including each redirect and TLS SNI."""
    for _ in range(6):
        normalized, pinned = destination(url, approved_hosts)
        parts = urlsplit(normalized)
        with httpx.Client(
            timeout=10, trust_env=False, follow_redirects=False
        ) as client:
            with client.stream(
                "GET",
                pinned,
                headers={"Host": parts.netloc, "User-Agent": "AnalystIngestion/1.0"},
                extensions={"sni_hostname": parts.hostname},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise CrawlError("Redirect has no destination")
                    url = normalize_url(urljoin(normalized, location))
                    destination(url, approved_hosts)
                    if redirect_policy is not None and not redirect_policy(url):
                        raise CrawlError("Robots policy forbids redirect destination")
                    continue
                if response.status_code >= 400:
                    raise CrawlError(
                        f"HTTP fetch failed with status {response.status_code}"
                    )
                content = bytearray()
                for block in response.iter_bytes():
                    content.extend(block)
                    if len(content) > byte_limit:
                        raise CrawlError("Crawl byte limit exceeded")
                return Page(
                    normalized,
                    bytes(content),
                    response.headers.get("content-type", "").split(";", 1)[0],
                )
    raise CrawlError("Too many redirects")


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and len(self.links) < 1000:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def crawl(
    request: CrawlRequest,
    hosts: list[str],
    *,
    fetcher: Callable[[str, list[str], int], Page] = fetch,
    progress: Callable[[dict[str, Any]], None] | None = None,
    delay: float = 0.2,
) -> list[Page]:
    start = normalize_url(request.url)
    if urlsplit(start).hostname not in hosts:
        raise CrawlError("Destination host is not approved")
    robots: dict[str, RobotFileParser] = {}
    seen: set[str] = set()
    pages: list[Page] = []
    queue = deque([(start, 0)])
    remaining = request.max_bytes
    visited = 0
    deadline = time.monotonic() + 120
    last_fetch: dict[str, float] = {}

    def read(url: str, *, robots_request: bool = False) -> Page:
        nonlocal remaining
        if remaining < 1 or time.monotonic() > deadline:
            raise CrawlError("Crawl byte or time limit exceeded")
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        minimum_delay = delay
        policy = robots.get(origin)
        if policy is not None:
            minimum_delay = max(
                minimum_delay, float(policy.crawl_delay("AnalystIngestion") or 0)
            )
            rate = policy.request_rate("AnalystIngestion")
            if rate is not None and rate.requests > 0:
                minimum_delay = max(minimum_delay, rate.seconds / rate.requests)
        pause = max(
            delay, minimum_delay - (time.monotonic() - last_fetch.get(origin, 0))
        )
        if pause > 60 or pause >= deadline - time.monotonic():
            raise CrawlError("Robots rate delay exceeds the crawl time budget")
        time.sleep(pause)
        last_fetch[origin] = time.monotonic()
        if fetcher is fetch:
            result = fetch(
                url,
                hosts,
                remaining,
                redirect_policy=None if robots_request else allowed,
            )
        else:
            result = fetcher(url, hosts, remaining)
        if len(result.content) > remaining:
            raise CrawlError("Crawl byte limit exceeded")
        remaining -= len(result.content)
        return result

    def allowed(url: str) -> bool:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin not in robots:
            # Fail closed when robots cannot be read.
            result = read(origin + "/robots.txt", robots_request=True)
            if urlsplit(result.url).netloc != parts.netloc:
                raise CrawlError("Cross-origin robots redirect is forbidden")
            parser = RobotFileParser()
            parser.parse(result.content.decode("utf-8", errors="replace").splitlines())
            robots[origin] = parser
        return robots[origin].can_fetch("AnalystIngestion", url)

    if request.sitemap:
        if not allowed(start):
            raise CrawlError("Robots policy forbids sitemap")
        sitemap = read(start)
        if sitemap.media_type not in {"application/xml", "text/xml"}:
            raise CrawlError("Sitemap must be XML")
        try:
            root = fromstring(sitemap.content)
        except Exception as error:
            raise CrawlError("Invalid sitemap XML") from error
        if root.tag.rsplit("}", 1)[-1] != "urlset":
            raise CrawlError(
                "Sitemap indexes are not supported; supply a bounded URL set"
            )
        queue.clear()
        for item in root.iter():
            if (
                item.tag.rsplit("}", 1)[-1] == "loc"
                and item.text
                and len(queue) < request.max_pages
            ):
                queue.append((item.text.strip(), 0))

    while queue and len(pages) < request.max_pages and visited < request.max_pages * 4:
        raw, depth = queue.popleft()
        try:
            url = normalize_url(raw)
            if url in seen:
                continue
            seen.add(url)
            visited += 1
            if urlsplit(url).hostname not in hosts:
                raise CrawlError("Destination host is not approved")
            if not allowed(url):
                raise CrawlError("Robots policy forbids page")
            page = read(url)
            if page.url != url:
                if page.url in seen:
                    continue
                if not allowed(page.url):
                    raise CrawlError("Robots policy forbids redirect destination")
            seen.add(page.url)
            if page.media_type not in {"text/html", "application/xhtml+xml"}:
                raise CrawlError("Only HTML pages can be ingested")
            pages.append(page)
            if progress:
                progress({"url": url, "state": "fetched", "bytes": len(page.content)})
            if depth < request.max_depth:
                parser = Links()
                parser.feed(page.content.decode("utf-8", errors="replace"))
                for link in parser.links:
                    if len(queue) >= request.max_pages * 4:
                        break
                    queue.append((urljoin(page.url, link), depth + 1))
        except (CrawlError, ValueError, httpx.HTTPError) as error:
            if progress:
                progress({"url": raw[:2048], "state": "failed", "message": str(error)})
    return pages
