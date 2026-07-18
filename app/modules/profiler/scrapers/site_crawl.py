import asyncio
from urllib.parse import urlparse
from app.core.logger import get_logger
from scrapling.spiders import Spider
from scrapling.fetchers import StealthyFetcher

logger = get_logger(__name__)

class LeadSiteSpider(Spider):
    name = "lead_site_spider"
    
    # We will override these in __init__
    def __init__(self, domain, max_pages=15, max_depth=2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not domain.startswith('http'):
            domain = 'https://' + domain
        self.start_urls = [domain]
        self.target_domain = urlparse(domain).netloc
        self.allowed_domains = [self.target_domain]
        
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.robots_txt_obey = True
        self.concurrent_requests_per_domain = 2
        self.download_delay = 1.0 # 1 second delay
        
        self.crawled_count = 0
        self.queued_count = 1  # start URL is queued
        self.results = []
        
        self.priority_keywords = [
            'about', 'team', 'services', 'pricing', 'careers', 
            'case-studies', 'case', 'studies', 'blog', 'contact'
        ]
        self.deny_keywords = [
            'login', 'signin', 'cart', 'checkout', 'privacy', 'terms', 'policy', 'legal'
        ]

    def _should_follow(self, url: str) -> bool:
        """Check if URL looks like a priority page and not in denylist."""
        lower_url = url.lower()
        for deny in self.deny_keywords:
            if deny in lower_url:
                return False
        return True

    def _score_url(self, url: str, text: str) -> int:
        """Score URL for priority queueing (not strictly queueing, but helps)."""
        score = 0
        lower_url = url.lower()
        lower_text = text.lower()
        for kw in self.priority_keywords:
            if kw in lower_url or kw in lower_text:
                score += 1
        return score

    async def parse(self, response):
        if self.crawled_count >= self.max_pages:
            return

        self.crawled_count += 1
        
        title = response.css('title').extract_first() or ""
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.body, "html.parser")
        for tag in soup(["nav", "header", "footer", "script", "style", "noscript", "aside"]):
            tag.decompose()
        
        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text_content = "\n".join(lines)[:3000] # Cap length per page
        
        item = {
            "url": response.url,
            "title": title.strip(),
            "text_content": text_content
        }
        self.results.append(item)
        yield item

        # Follow links
        depth = response.meta.get('depth', 0) if hasattr(response, 'meta') and isinstance(response.meta, dict) else 0
        
        if depth < self.max_depth and self.queued_count < self.max_pages:
            links = response.css('a')
            
            link_objs = []
            base_target = self.target_domain.replace('www.', '')
            for link in links:
                href = link.attrib.get('href')
                text = link.text or ""
                if href:
                    url = response.urljoin(href)
                    link_domain = urlparse(url).netloc.replace('www.', '')
                    if link_domain == base_target and self._should_follow(url):
                        score = self._score_url(url, text)
                        link_objs.append((score, url))
            
            link_objs.sort(key=lambda x: x[0], reverse=True)
            
            for score, url in link_objs:
                if self.queued_count >= self.max_pages:
                    break
                self.queued_count += 1
                yield response.follow(url, callback=self.parse, meta={'depth': depth + 1})

async def crawl_lead_site(domain: str, max_pages: int = 15, max_depth: int = 2) -> list[dict]:
    spider = LeadSiteSpider(domain=domain, max_pages=max_pages, max_depth=max_depth)
    try:
        await asyncio.to_thread(spider.start)
    except Exception as e:
        logger.warning(f"Crawl failed for {domain}: {e}")
    
    return spider.results
