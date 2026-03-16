from scrapers.playwright_scraper import PlaywrightScraper
from scrapers.bs4_scraper import BS4Scraper
from scrapers.scrapy_scraper import ScrapyScraper
from scrapers.puppeteer_scraper import PuppeteerScraper
from scrapers.crawl4ai_scraper import Crawl4AIScraper
from scrapers.base import BaseScraper

ENGINES: dict[str, type[BaseScraper]] = {
    "playwright": PlaywrightScraper,
    "beautifulsoup": BS4Scraper,
    "scrapy": ScrapyScraper,
    "puppeteer": PuppeteerScraper,
    "crawl4ai": Crawl4AIScraper,
}


def get_engine(name: str) -> BaseScraper:
    cls = ENGINES.get(name)
    if not cls:
        raise ValueError(f"Unknown engine '{name}'. Available: {list(ENGINES.keys())}")
    return cls()
