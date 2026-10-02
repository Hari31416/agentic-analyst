from __future__ import annotations

import socket
from collections.abc import Callable

import httpx
import pytest

from app.ingestion import crawl as crawler


def _dns(monkeypatch: pytest.MonkeyPatch, records: dict[str, list[str]]) -> list[str]:
    queried: list[str] = []

    def getaddrinfo(host: str, port: int, **kwargs: object):
        queried.append(host)
        addresses = records.get(host)
        if addresses is None:
            raise socket.gaierror("unknown host")
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port, 0, 0) if ":" in address else (address, port),
            )
            for address in addresses
        ]

    monkeypatch.setattr(crawler.socket, "getaddrinfo", getaddrinfo)
    return queried


def _page(url: str, body: str, media_type: str = "text/html") -> crawler.Page:
    return crawler.Page(url, body.encode(), media_type)


def _site_fetcher(
    pages: dict[str, crawler.Page],
) -> Callable[[str, list[str], int], crawler.Page]:
    def fetcher(url: str, hosts: list[str], byte_limit: int) -> crawler.Page:
        del hosts
        page = pages[url]
        if len(page.content) > byte_limit:
            raise crawler.CrawlError("Crawl byte limit exceeded")
        return page

    return fetcher


def test_destination_rejects_private_and_mixed_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dns(
        monkeypatch,
        {
            "private.example": ["10.0.0.8"],
            "mixed.example": ["93.184.216.34", "127.0.0.1"],
        },
    )

    with pytest.raises(crawler.CrawlError, match="forbidden address"):
        crawler.destination("https://private.example/", ["private.example"])
    with pytest.raises(crawler.CrawlError, match="forbidden address"):
        crawler.destination("https://mixed.example/", ["mixed.example"])


def test_destination_requires_approved_host_and_standard_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dns(monkeypatch, {"approved.example": ["93.184.216.34"]})

    with pytest.raises(crawler.CrawlError, match="not approved"):
        crawler.destination("https://approved.example/", ["other.example"])
    with pytest.raises(crawler.CrawlError, match="Credentials"):
        crawler.normalize_url("https://user:secret@approved.example/")
    with pytest.raises(crawler.CrawlError, match="nonstandard ports"):
        crawler.normalize_url("https://approved.example:8443/")

    normalized, pinned = crawler.destination(
        "https://approved.example:443/report?q=1#fragment", ["approved.example"]
    )
    assert normalized == "https://approved.example:443/report?q=1"
    assert pinned == "https://93.184.216.34:443/report?q=1"


def test_fetch_pins_each_redirect_destination_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queried = _dns(
        monkeypatch,
        {
            "start.example": ["93.184.216.34"],
            "landing.example": ["1.1.1.1"],
        },
    )
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["host"] == "start.example":
            return httpx.Response(
                302, headers={"location": "https://landing.example/final"}
            )
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, content=b"done"
        )

    transport = httpx.MockTransport(respond)
    original_client = httpx.Client

    def client_factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(crawler.httpx, "Client", client_factory)
    page = crawler.fetch(
        "https://start.example/", ["start.example", "landing.example"], 100
    )

    assert page.url == "https://landing.example/final"
    assert page.content == b"done"
    assert queried == ["start.example", "landing.example", "landing.example"]
    assert [request.url.host for request in requests] == ["93.184.216.34", "1.1.1.1"]
    assert [request.headers["host"] for request in requests] == [
        "start.example",
        "landing.example",
    ]
    assert requests[0].extensions["sni_hostname"] == "start.example"
    assert requests[1].extensions["sni_hostname"] == "landing.example"


def test_fetch_rechecks_redirect_and_rejects_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dns(
        monkeypatch,
        {"start.example": ["93.184.216.34"], "private.example": ["192.168.1.20"]},
    )
    original_client = httpx.Client
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, headers={"location": "https://private.example/secret"}
        )
    )
    monkeypatch.setattr(
        crawler.httpx,
        "Client",
        lambda *args, **kwargs: original_client(*args, transport=transport, **kwargs),
    )

    with pytest.raises(crawler.CrawlError, match="forbidden address"):
        crawler.fetch(
            "https://start.example/", ["start.example", "private.example"], 100
        )


def test_crawl_respects_robots_disallow_and_reports_failure() -> None:
    start = "https://docs.example/"
    pages = {
        "https://docs.example/robots.txt": crawler.Page(
            "https://docs.example/robots.txt",
            b"User-agent: AnalystIngestion\nDisallow: /private\n",
            "text/plain",
        ),
        start: _page(
            start, '<a href="/private/secret">secret</a><a href="/public">public</a>'
        ),
        "https://docs.example/private/secret": _page(
            "https://docs.example/private/secret", "secret"
        ),
        "https://docs.example/public": _page("https://docs.example/public", "public"),
    }
    events: list[dict[str, object]] = []

    result = crawler.crawl(
        crawler.CrawlRequest(url=start, max_pages=5, max_depth=1),
        ["docs.example"],
        fetcher=_site_fetcher(pages),
        progress=events.append,
        delay=0,
    )

    assert [page.url for page in result] == [start, "https://docs.example/public"]
    assert any(
        event.get("state") == "failed" and "Robots" in str(event) for event in events
    )
    assert all("private/secret" not in page.url for page in result)


def test_sitemap_urlset_is_bounded_and_indexes_are_rejected() -> None:
    origin = "https://docs.example"
    robots = crawler.Page(
        origin + "/robots.txt", b"User-agent: *\nAllow: /\n", "text/plain"
    )
    sitemap_url = origin + "/sitemap.xml"
    sitemap = crawler.Page(
        sitemap_url,
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://docs.example/a</loc></url>"
        b"<url><loc>https://docs.example/b</loc></url>"
        b"<url><loc>https://docs.example/c</loc></url></urlset>",
        "application/xml",
    )
    pages = {
        origin + "/robots.txt": robots,
        sitemap_url: sitemap,
        origin + "/a": _page(origin + "/a", "A"),
        origin + "/b": _page(origin + "/b", "B"),
        origin + "/c": _page(origin + "/c", "C"),
    }
    result = crawler.crawl(
        crawler.CrawlRequest(url=sitemap_url, sitemap=True, max_pages=2),
        ["docs.example"],
        fetcher=_site_fetcher(pages),
        delay=0,
    )
    assert [page.url for page in result] == [origin + "/a", origin + "/b"]

    index = crawler.Page(
        sitemap_url,
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></sitemapindex>',
        "application/xml",
    )
    with pytest.raises(crawler.CrawlError, match="indexes are not supported"):
        crawler.crawl(
            crawler.CrawlRequest(url=sitemap_url, sitemap=True),
            ["docs.example"],
            fetcher=_site_fetcher({origin + "/robots.txt": robots, sitemap_url: index}),
            delay=0,
        )


def test_crawl_enforces_byte_page_depth_and_loop_bounds() -> None:
    origin = "https://docs.example"
    robots_url = origin + "/robots.txt"
    pages = {
        robots_url: crawler.Page(
            robots_url, b"User-agent: *\nAllow: /\n", "text/plain"
        ),
        origin
        + "/start": _page(
            origin + "/start",
            '<a href="/child">child</a><a href="/start#again">loop</a>',
        ),
        origin
        + "/child": _page(origin + "/child", '<a href="/grandchild">grandchild</a>'),
        origin + "/grandchild": _page(origin + "/grandchild", "grandchild"),
    }
    fetcher = _site_fetcher(pages)
    events: list[dict[str, object]] = []
    result = crawler.crawl(
        crawler.CrawlRequest(url=origin + "/start", max_pages=10, max_depth=1),
        ["docs.example"],
        fetcher=fetcher,
        progress=events.append,
        delay=0,
    )
    assert [page.url for page in result] == [origin + "/start", origin + "/child"]

    byte_events: list[dict[str, object]] = []
    limited = crawler.crawl(
        crawler.CrawlRequest(
            url=origin + "/start", max_pages=10, max_depth=2, max_bytes=60
        ),
        ["docs.example"],
        fetcher=fetcher,
        progress=byte_events.append,
        delay=0,
    )
    assert sum(len(page.content) for page in limited) <= 60
    assert any(
        event.get("state") == "failed" and "byte limit" in str(event).lower()
        for event in byte_events
    )
    assert len([page for page in result if page.url == origin + "/start"]) == 1

    page_limited = crawler.crawl(
        crawler.CrawlRequest(url=origin + "/start", max_pages=1, max_depth=3),
        ["docs.example"],
        fetcher=fetcher,
        delay=0,
    )
    assert [page.url for page in page_limited] == [origin + "/start"]


def test_crawl_honors_robots_delay_without_real_wait(monkeypatch):
    ticks = [1000.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        ticks[0] += seconds

    monkeypatch.setattr(crawler.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(crawler.time, "sleep", sleep)

    def fetcher(url, hosts, limit):
        if url.endswith("/robots.txt"):
            return crawler.Page(
                url, b"User-agent: *\nAllow: /\nCrawl-delay: 2\n", "text/plain"
            )
        return crawler.Page(url, b"<p>Allowed page</p>", "text/html")

    pages = crawler.crawl(
        crawler.CrawlRequest(url="https://approved.example/", max_pages=1),
        ["approved.example"],
        fetcher=fetcher,
    )
    assert len(pages) == 1
    assert sleeps[-1] >= 2
