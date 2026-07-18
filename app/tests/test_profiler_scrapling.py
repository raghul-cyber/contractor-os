import pytest
import asyncio
from typing import Dict, Any
import app.modules.profiler.scrapers.website as website_mod
import app.modules.profiler.scrapers.site_crawl as crawl_mod

class MockResponse:
    def __init__(self, status=200, body=b"", text=""):
        self.status = status
        self.body = body
        self.text = text

@pytest.mark.asyncio
async def test_website_fallback_to_stealthy(monkeypatch):
    """Assert Fetcher blocked/empty response escalates to StealthyFetcher."""
    fetcher_called = False
    stealth_called = False
    
    class MockFetcher:
        def get(self, url):
            nonlocal fetcher_called
            fetcher_called = True
            # Return a blocked Cloudflare response
            return MockResponse(
                status=403, 
                body=b"Please wait while we verify you are human",
                text="Please wait while we verify you are human cloudflare"
            )

    class MockStealthy:
        def fetch(self, url):
            nonlocal stealth_called
            stealth_called = True
            # Return good response
            html = b"<html><body>Good Content</body></html>"
            return MockResponse(status=200, body=html, text="Good Content")

    monkeypatch.setattr(website_mod, "Fetcher", MockFetcher)
    monkeypatch.setattr(website_mod, "StealthyFetcher", MockStealthy)
    
    # We also mock _fetch_url so it doesn't try to fetch about/services
    async def mock_fetch_url(url, use_stealth=False):
        return ""
    monkeypatch.setattr(website_mod, "_fetch_url", mock_fetch_url)
    
    res = await website_mod.scrape_website("https://example.com")
    
    assert fetcher_called, "Should try regular fetcher first"
    assert stealth_called, "Should escalate to stealthy fetcher on bot challenge"
    assert "Good Content" in res["homepage"]

@pytest.mark.asyncio
async def test_website_dead_domain(monkeypatch):
    """Assert completely dead domains don't raise and return partial/empty data."""
    class BadFetcher:
        def get(self, url): raise ConnectionError("Dead domain")
        def fetch(self, url): raise ConnectionError("Dead domain")
        
    monkeypatch.setattr(website_mod, "Fetcher", BadFetcher)
    monkeypatch.setattr(website_mod, "StealthyFetcher", BadFetcher)
    
    # Even if _fetch_url is called, it should also fail gracefully
    async def bad_fetch(url, use_stealth=False):
        return ""
    monkeypatch.setattr(website_mod, "_fetch_url", bad_fetch)
    
    res = await website_mod.scrape_website("https://deaddomain.com")
    
    assert res["homepage"] == ""
    assert res["about"] == ""
    assert res["services"] == ""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
import socket

def get_free_port():
    s = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM)
    s.bind(('localhost', 0))
    address, port = s.getsockname()
    s.close()
    return port

class MockSiteHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Suppress logging
        
    def do_GET(self):
        # 1. Robots.txt
        if self.path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *\nDisallow: /admin\n")
            return
            
        # 2. Homepage (with off-domain links, and allowed/disallowed links)
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            html = b"""
            <html>
                <head><title>Home</title></head>
                <body>
                    Welcome to home.
                    <a href="/about">About</a>
                    <a href="/admin">Admin (Disallowed)</a>
                    <a href="https://external.com/page">External (Off-domain)</a>
                    <a href="/failpage">Fail Page</a>
                    <a href="/deep1">Deep 1</a>
                </body>
            </html>
            """
            self.wfile.write(html)
            return
            
        # 3. About (allowed)
        if self.path == "/about":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><head><title>About</title></head><body>About Us Content</body></html>")
            return
            
        # 4. Admin (Disallowed by robots.txt)
        if self.path == "/admin":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Admin Secret</body></html>")
            return
            
        # 5. Fail Page (Single-page failure)
        if self.path == "/failpage":
            self.send_response(500)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Server Error</body></html>")
            return
            
        # 6. Deep Pages (for max_depth/max_pages test)
        if self.path.startswith("/deep"):
            num = int(self.path.replace("/deep", ""))
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            # Infinite chain
            self.wfile.write(f"<html><head><title>Deep {num}</title></head><body>Deep {num} <a href='/deep{num+1}'>Next</a></body></html>".encode())
            return
            
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

@pytest.fixture(scope="module")
def mock_server():
    port = get_free_port()
    server = HTTPServer(('localhost', port), MockSiteHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield f"http://localhost:{port}"
    server.shutdown()
    server.server_close()

@pytest.mark.asyncio
async def test_spider_crawl_rules(mock_server):
    """
    Tests robots.txt compliance, off-domain rejection, single-page failure resilience, 
    and max_pages / max_depth caps.
    """
    domain = mock_server
    
    # We will set max_pages=6, max_depth=2 for this test
    # The home page is depth 0.
    # /about, /admin, /failpage, /deep1 are depth 1.
    # /deep1 links to /deep2 (depth 2).
    # /deep2 links to /deep3 (depth 3) which should NOT be visited due to max_depth=2.
    # Total pages to crawl should not exceed max_pages.
    
    results = await crawl_mod.crawl_lead_site(domain, max_pages=6, max_depth=2)
    
    urls_crawled = [res["url"].replace(domain, "") for res in results]
    urls_crawled_set = set(urls_crawled)
    
    # 1. Check home page
    assert "/" in urls_crawled_set, "Should have crawled home"
    
    # 2. Check About page (allowed)
    assert "/about" in urls_crawled_set, "Should have crawled About"
    
    # 3. Check robots.txt compliance
    assert "/admin" not in urls_crawled_set, "Should have respected robots.txt and ignored /admin"
    
    # 4. Check off-domain rejection
    # The external.com link should not be present in our results list because Spider drops it
    # We assert no external domains exist in results
    for u in urls_crawled:
        assert not u.startswith("http") or u.startswith(domain), "Crawled an off-domain URL!"
        
    # 5. Check single-page failure resilience
    # /failpage returns 500. Depending on Scrapling's handling, it might ignore it or save empty. 
    # But it definitely shouldn't crash the whole spider.
    # The fact that we have other results proves it survived.
    assert len(results) >= 2, "Spider crashed and returned nothing!"
    
    # 6. Check max_depth and max_pages
    assert "/deep1" in urls_crawled_set, "Should have reached depth 1"
    assert "/deep2" in urls_crawled_set, "Should have reached depth 2"
    assert "/deep3" not in urls_crawled_set, "Should NOT have reached depth 3 (max_depth=2)"
    
    # max_pages cap
    assert len(results) <= 6, f"Should not exceed max_pages (got {len(results)})"
