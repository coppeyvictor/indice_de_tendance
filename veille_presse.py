import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import feedparser
import requests
from bs4 import BeautifulSoup

from sentiment import (
    QuotaExceededError,
    analyze_sentiment,
    analyze_sentiment_local_details,
    prepare_sentiment_chunks,
)


DATABASE_PATH = os.getenv("SENTIMENT_DATABASE", "sentiments.db")
MAX_ARTICLES_PER_DAY = int(os.getenv("MAX_ARTICLES_PER_DAY", "0"))
UNLIMITED_DAILY_ARTICLES_FROM = date.min
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
ARTICLE_DELAY_SECONDS = (
    0.0
    if os.getenv("SENTIMENT_PROVIDER", "local").lower() == "local"
    else float(os.getenv("ARTICLE_DELAY_SECONDS", "5"))
)
RSS_COLLECTION_WINDOW_DAYS = max(1, int(os.getenv("RSS_COLLECTION_WINDOW_DAYS", "3")))
RSS_MAX_WORKERS = max(1, int(os.getenv("RSS_MAX_WORKERS", "4")))
BASE_RSS_URLS = (
    "https://www.ft.com/?format=rss",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.bloomberg.com/feeds/markets/news.rss",
    "https://www.investing.com/rss/news_25.rss",
    "https://www.economist.com/finance-and-economics/rss.xml",
    "https://www.theguardian.com/business/rss",
    "https://www.ft.com/world/europe?format=rss",
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.scmp.com/rss/2/feed",
    "https://www.lemonde.fr/rss/tag/economie.xml",
    "https://www.lemonde.fr/rss/tag/entreprises.xml",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://cryptoslate.com/feed/",
    "https://blog.kraken.com/feed/",
    "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
)
CRYPTO_RSS_URLS = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://www.theblock.co/rss",
    "https://decrypt.co/feed",
    "https://cryptoslate.com/feed/",
    "https://blog.kraken.com/feed/",
)
MARKETS_RSS_URLS = (
    "https://feeds.reuters.com/reuters/marketsNews",
    "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://www.investing.com/rss/news_25.rss",
    "https://seekingalpha.com/api/sa/combined/latest.xml",
    "https://www.barrons.com/rss/headlines",
)
DAILY_TOPIC_SEARCHES = (
    "stock markets OR bonds OR investment",
    "central bank OR inflation OR interest rates",
    "companies OR business OR economy",
    "oil prices OR commodities OR trade",
)
TARGETED_TOPIC_SEARCHES = (
    "inflation OR rates OR fed OR central bank",
    "energy OR oil OR gas OR commodities",
    "housing OR mortgages OR real estate",
    "semiconductors OR AI OR tech earnings",
    "banking OR credit OR debt OR loans",
    "forex OR dollar OR euro OR yuan",
    "crypto OR bitcoin OR ethereum OR stablecoins",
    "trade OR tariffs OR exports OR manufacturing",
)
THEME_SEARCHES = {
    "finance": (
        "stocks OR bonds OR rates OR inflation OR central bank",
        "equities OR credit OR banks OR forex OR commodities",
        "market volatility OR earnings OR investment strategy",
    ),
    "esg": (
        "green bonds OR sustainable finance OR climate risk",
        "EU taxonomy OR CSRD OR ESG reporting OR impact investing",
        "carbon market OR carbon price OR emissions trading",
    ),
    "ecology": (
        "climate change OR renewable energy OR clean energy",
        "carbon capture OR net zero OR decarbonization",
        "biodiversity OR deforestation OR pollution OR clean technology",
    ),
}
THEME_DIRECT_RSS_URLS = {
    "esg": (
        "https://www.esgtoday.com/feed/",
        "https://www.edie.net/feed/",
    ),
    "ecology": (
        "https://news.mongabay.com/feed/",
        "https://insideclimatenews.org/feed/",
        "https://www.carbonbrief.org/feed/",
        "https://grist.org/feed/",
    ),
}
GOOGLE_NEWS_LOCALES = (
    ("en-US", "US", "en"),
    ("en-GB", "GB", "en"),
    ("fr-FR", "FR", "fr"),
    ("de-DE", "DE", "de"),
)


def live_google_news_urls() -> tuple[str, ...]:
    """Build multilingual searches covering finance, ESG and ecology headlines."""
    searches = DAILY_TOPIC_SEARCHES + TARGETED_TOPIC_SEARCHES
    urls = []
    for theme_searches in THEME_SEARCHES.values():
        searches = searches + theme_searches
    for search in searches:
        for language, country, feed_language in GOOGLE_NEWS_LOCALES:
            query = f"{search} when:{RSS_COLLECTION_WINDOW_DAYS}d"
            urls.append(
                "https://news.google.com/rss/search?q="
                f"{quote(query)}&hl={language}&gl={country}"
                f"&ceid={country}:{feed_language}"
            )
    return tuple(urls)


DEFAULT_RSS_URLS = tuple(
    dict.fromkeys(
        BASE_RSS_URLS
        + live_google_news_urls()
    )
)
RSS_URLS = tuple(
    url.strip()
    for url in os.getenv("RSS_URLS", ",".join(DEFAULT_RSS_URLS)).split(",")
    if url.strip()
)
RSS_TIMEOUT_SECONDS = float(os.getenv("RSS_TIMEOUT_SECONDS", "10"))
CATEGORIES = ("economy", "markets", "crypto")
THEMES = ("finance", "esg", "ecology")
THEME_LABELS = {
    "finance": "Finance",
    "esg": "ESG",
    "ecology": "Ecology",
}
ASSET_TYPES = (
    "macroeconomy",
    "equities",
    "fixed_income",
    "crypto",
    "real_estate",
    "commodities",
    "forex",
    "business",
)


def classify_theme(title: str, summary: str = "", source: str = "") -> str:
    """Classify a news item into the major thematic families used by the dashboard."""
    text = f"{title} {summary} {source}".lower()
    esg_terms = (
        "esg", "sustainable finance", "green bond", "green bonds", "sustainability reporting",
        "csrd", "eu taxonomy", "climate risk", "carbon market", "carbon price",
        "emissions trading", "net zero", "decarbonization", "transition finance",
        "responsible investment", "climate policy", "climate regulation",
        "sustainable investing", "social governance", "impact investing",
    )
    ecology_terms = (
        "climate", "carbon", "emissions", "emission", "biodiversity", "deforestation",
        "renewable", "wind turbine", "solar", "hydrogen", "electric vehicle", "evs",
        "pollution", "ocean", "water stress", "wildlife", "clean energy", "carbon capture",
        "warming", "greenhouse gas",
    )
    finance_terms = (
        "stock", "stocks", "market", "markets", "shares", "equity", "bond", "bonds",
        "investor", "investment", "fund", "indices", "index", "wall street",
        "nasdaq", "dow jones", "s&p 500", "central bank", "fed", "inflation", "rates",
        "yield", "credit", "banking", "forex", "currency",
    )
    if any(term in text for term in esg_terms):
        return "esg"
    if any(term in text for term in ecology_terms):
        return "ecology"
    if any(term in text for term in finance_terms):
        return "finance"
    return "finance"


def classify_country(title: str, summary: str = "", source: str = "") -> str:
    """Return a country code when the article contains a reliable country clue."""
    text = f"{title} {summary}".lower()
    country_terms = {
        "US": ("united states", "u.s.", "us economy", "american", "washington", "wall street"),
        "GB": ("united kingdom", "u.k.", "british", "england", "london", "bank of england"),
        "FR": ("france", "french", "paris", "banque de france"),
        "DE": ("germany", "german", "berlin", "bundesbank"),
        "CN": ("china", "chinese", "beijing", "shanghai", "people's bank of china"),
        "JP": ("japan", "japanese", "tokyo", "bank of japan"),
        "IN": ("india", "indian", "mumbai", "reserve bank of india"),
        "CA": ("canada", "canadian", "toronto", "bank of canada"),
        "AU": ("australia", "australian", "sydney", "reserve bank of australia"),
        "EU": ("eurozone", "european union", "european central bank", "ecb"),
    }
    for country, terms in country_terms.items():
        if any(term in text for term in terms):
            return country

    source_text = source.lower()
    source_countries = {
        "bbc": "GB",
        "financial times": "GB",
        "le monde": "FR",
        "scmp": "CN",
        "cnbc": "US",
        "new york times": "US",
        "coindesk": "US",
        "cointelegraph": "US",
    }
    for source_name, country in source_countries.items():
        if source_name in source_text:
            return country
    return "INT"


def classify_asset_type(title: str, summary: str = "") -> str:
    """Return the most specific financial asset or topic type found in the article."""
    text = f"{title} {summary}".lower()
    asset_terms = (
        ("crypto", ("bitcoin", "crypto", "cryptocurrency", "ethereum", "blockchain", "stablecoin", "token", "defi", "web3")),
        ("real_estate", ("real estate", "property", "housing", "home prices", "mortgage")),
        ("commodities", ("oil", "gold", "copper", "commodity", "commodities", "natural gas")),
        ("fixed_income", ("bond", "bonds", "treasury", "yield", "credit spread")),
        ("forex", ("forex", "currency", "currencies", "exchange rate", "dollar", "euro")),
        ("equities", ("stock", "stocks", "shares", "equity", "equities", "nasdaq", "s&p 500", "wall street")),
        ("business", ("company", "companies", "earnings", "profit", "merger", "acquisition")),
    )
    for asset_type, terms in asset_terms:
        if any(term in text for term in terms):
            return asset_type
    return "macroeconomy"


def classify_article(title: str, summary: str = "", source: str = "") -> str:
    """Classify an article into one of the three index families."""
    text = f"{title} {summary} {source}".lower()
    crypto_terms = (
        "bitcoin", "crypto", "cryptocurrency", "ethereum", "blockchain",
        "stablecoin", "token", "defi", "web3", "coinbase", "binance",
    )
    market_terms = (
        "stock", "stocks", "market", "markets", "shares", "equity",
        "bond", "bonds", "investor", "investment", "fund", "indices",
        "index", "wall street", "nasdaq", "dow jones", "s&p 500",
    )
    if any(term in text for term in crypto_terms):
        return "crypto"
    if any(term in text for term in market_terms):
        return "markets"
    return "economy"


def normalize_article_url(url: str) -> str | None:
    """Reject Google News redirect URLs and return a usable article URL."""
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None

    normalized = cleaned.split("#", 1)[0].split("?", 1)[0]
    maybe_google = "news.google.com" in normalized.lower()
    if maybe_google:
        if "/rss/articles/" in normalized.lower() or "/rss/search?" in normalized.lower() or "/rss/search?q=" in normalized.lower():
            return None
        if "/articles/" in normalized.lower() or "/search" in normalized.lower():
            return None

    return cleaned


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def console_print(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    try:
        print(message, file=stream)
    except UnicodeEncodeError:
        fallback = message.encode("ascii", errors="backslashreplace").decode(
            "ascii"
        )
        print(fallback, file=stream)


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(total_seconds, 60)

    if minutes:
        return f"{minutes} min {remaining_seconds} s"
    return f"{remaining_seconds} s"


def initialize_database() -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                published TEXT,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                score REAL NOT NULL,
                analyzed_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_articles (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                title_fr TEXT NOT NULL DEFAULT '',
                title_en TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL,
                published TEXT,
                source TEXT NOT NULL,
                article_day TEXT NOT NULL,
                content TEXT,
                score REAL,
                impact_financier REAL,
                confiance REAL,
                analyzed_at TEXT,
                category TEXT NOT NULL DEFAULT 'economy',
                country TEXT NOT NULL DEFAULT 'INT',
                asset_type TEXT NOT NULL DEFAULT 'macroeconomy'
            )
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(daily_articles)")
        }
        if "cluster_id" not in columns:
            connection.execute("ALTER TABLE daily_articles ADD COLUMN cluster_id TEXT")
        if "cluster_size" not in columns:
            connection.execute(
                "ALTER TABLE daily_articles ADD COLUMN cluster_size INTEGER"
            )
        if "category" not in columns:
            connection.execute(
                "ALTER TABLE daily_articles ADD COLUMN category TEXT NOT NULL DEFAULT 'economy'"
            )
        if "country" not in columns:
            connection.execute(
                "ALTER TABLE daily_articles ADD COLUMN country TEXT NOT NULL DEFAULT 'INT'"
            )
        if "asset_type" not in columns:
            connection.execute(
                "ALTER TABLE daily_articles ADD COLUMN asset_type TEXT NOT NULL DEFAULT 'macroeconomy'"
            )
        if "theme" not in columns:
            connection.execute(
                "ALTER TABLE daily_articles ADD COLUMN theme TEXT NOT NULL DEFAULT 'finance'"
            )
        if "title_fr" not in columns:
            connection.execute("ALTER TABLE daily_articles ADD COLUMN title_fr TEXT NOT NULL DEFAULT ''")
        if "title_en" not in columns:
            connection.execute("ALTER TABLE daily_articles ADD COLUMN title_en TEXT NOT NULL DEFAULT ''")
        if "impact_financier" not in columns:
            connection.execute("ALTER TABLE daily_articles ADD COLUMN impact_financier REAL")
        if "confiance" not in columns:
            connection.execute("ALTER TABLE daily_articles ADD COLUMN confiance REAL")
        connection.execute("UPDATE daily_articles SET title_fr = title WHERE title_fr = ''")
        connection.execute("UPDATE daily_articles SET title_en = title WHERE title_en = ''")
        rows = connection.execute(
            "SELECT url, title, summary, source FROM daily_articles"
        ).fetchall()
        connection.executemany(
            """
            UPDATE daily_articles
            SET category = ?, country = ?, asset_type = ?, theme = ?
            WHERE url = ?
            """,
            [
                (
                    classify_article(title, summary, source),
                    classify_country(title, summary, source),
                    classify_asset_type(title, summary),
                    classify_theme(title, summary, source),
                    url,
                )
                for url, title, summary, source in rows
            ],
        )


def get_articles(
    rss_urls: tuple[str, ...] = RSS_URLS,
    forced_article_day: str | None = None,
) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []

    for rss_url in rss_urls:
        console_print(f"Lecture du flux RSS : {rss_url}")
        try:
            response = requests.get(
                rss_url,
                timeout=RSS_TIMEOUT_SECONDS,
                headers={"User-Agent": "Mozilla/5.0 RSS sentiment collector"},
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except requests.RequestException as error:
            console_print(f"Flux ignoré ({type(error).__name__}) : {rss_url}", error=True)
            continue
        source = feed.feed.get("title", rss_url)
        console_print(f"Flux reçu : {source} ({len(feed.entries)} article(s))")

        for entry in feed.entries:
            raw_url = entry.get("link", "").strip()
            url = normalize_article_url(raw_url)
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()

            if title and url:
                published_parsed = entry.get("published_parsed")
                if forced_article_day:
                    article_day = forced_article_day
                elif published_parsed:
                    article_day = datetime(
                        *published_parsed[:6], tzinfo=timezone.utc
                    ).date().isoformat()
                else:
                    article_day = datetime.now(timezone.utc).date().isoformat()

                articles.append(
                    {
                        "url": url,
                        "title": title,
                        "summary": summary,
                        "published": entry.get("published", ""),
                        "source": source,
                        "article_day": article_day,
                    }
                )

    return articles


def article_already_analyzed(url: str) -> bool:
    with sqlite3.connect(DATABASE_PATH) as connection:
        result = connection.execute(
            "SELECT 1 FROM articles WHERE url = ? LIMIT 1",
            (url,),
        ).fetchone()

    return result is not None


def extract_article_text(url: str) -> str:
    response = requests.get(
        url,
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    return soup.get_text(" ", strip=True)


def select_content(article: dict[str, str]) -> str:
    if article["summary"]:
        summary = BeautifulSoup(article["summary"], "html.parser").get_text(
            " ", strip=True
        )
        return re.sub(r"https?://\S+", "", summary).strip()

    return extract_article_text(article["url"])


HISTORICAL_SEARCHES = (
    "crypto OR bitcoin OR ethereum OR blockchain",
    "finance OR markets OR stocks OR investment",
    "business OR companies OR economy",
    "inflation OR interest rates OR central bank",
    "oil prices OR commodities OR trade",
    "climate risk OR green bonds OR sustainable finance",
    "renewable energy OR clean energy OR net zero",
    "carbon market OR emissions OR biodiversity",
)
HISTORICAL_LOCALES = (
    ("en-US", "US", "en"),
    ("en-CA", "CA", "en"),
    ("en-IN", "IN", "en"),
    ("en-SG", "SG", "en"),
    ("fr-FR", "FR", "fr"),
    ("en-GB", "GB", "en"),
)


def theme_rss_urls(theme: str = "finance") -> tuple[str, ...]:
    """Return direct editorial and Google News RSS URLs for a thematic family."""
    theme_key = (theme or "finance").lower()
    searches = THEME_SEARCHES.get(theme_key, THEME_SEARCHES["finance"])
    urls = list(THEME_DIRECT_RSS_URLS.get(theme_key, ()))
    for search in searches:
        for language, country, feed_language in GOOGLE_NEWS_LOCALES:
            query = f"{search} when:{RSS_COLLECTION_WINDOW_DAYS}d"
            urls.append(
                "https://news.google.com/rss/search?q="
                f"{quote(query)}&hl={language}&gl={country}"
                f"&ceid={country}:{feed_language}"
            )
    return tuple(dict.fromkeys(urls))


def historical_rss_urls(article_day: str) -> tuple[str, ...]:
    """Construit des flux Google News multilingues pour une journée historique."""
    urls = []
    for search in HISTORICAL_SEARCHES:
        query = f"{search} after:{article_day} before:{article_day}"
        for language, country, feed_language in HISTORICAL_LOCALES:
            urls.append(
                "https://news.google.com/rss/search?q="
                f"{quote(query)}&hl={language}&gl={country}"
                f"&ceid={country}:{feed_language}"
            )

    return tuple(urls)


def prune_daily_articles(article_day: str) -> int:
    if MAX_ARTICLES_PER_DAY <= 0:
        return 0
    if date.fromisoformat(article_day) >= UNLIMITED_DAILY_ARTICLES_FROM:
        return 0

    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            """
            SELECT url
            FROM daily_articles
            WHERE article_day = ?
            ORDER BY published DESC, url DESC
            LIMIT -1 OFFSET ?
            """,
            (article_day, MAX_ARTICLES_PER_DAY),
        ).fetchall()
        connection.executemany(
            "DELETE FROM daily_articles WHERE url = ?",
            rows,
        )

    return len(rows)


def translate_title(title: str, target_language: str) -> str:
    """Translate a title during ingestion, falling back only when necessary."""
    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={
                "langpair": f"autodetect|{target_language}",
                "q": title,
            },
            timeout=10,
            headers={"User-Agent": "Fear-and-Greed-study-dashboard"},
        )
        response.raise_for_status()
        payload = response.json()
        translated = payload.get("responseData", {}).get("translatedText", "").strip()
        if not translated or translated.upper().startswith("PLEASE SELECT"):
            return title
        return translated
    except (requests.RequestException, ValueError, TypeError, IndexError, KeyError):
        return title


def translate_title_pair(title: str) -> tuple[str, str]:
    """Return stable French and English display titles for an article."""
    return translate_title(title, "fr"), translate_title(title, "en")


def collect_daily_articles(
    article_day: str,
    rss_urls: tuple[str, ...] = RSS_URLS,
    forced_article_day: str | None = None,
    max_workers: int = RSS_MAX_WORKERS,
) -> None:
    target_day = date.fromisoformat(article_day)
    start_day = target_day - timedelta(days=RSS_COLLECTION_WINDOW_DAYS - 1)
    console_print(f"Collecte de {len(rss_urls)} flux RSS pour le {article_day}...")

    if max_workers <= 1:
        articles = get_articles(rss_urls, forced_article_day)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            batches = executor.map(
                lambda rss_url: get_articles((rss_url,), forced_article_day),
                rss_urls,
            )
            articles = [article for batch in batches for article in batch]

    articles = [
        article
        for article in articles
        if start_day <= date.fromisoformat(article["article_day"]) <= target_day
    ]

    unique_articles = {}
    for article in articles:
        unique_articles.setdefault(article["url"], article)

    translated_articles = []
    for article in unique_articles.values():
        title_fr, title_en = translate_title_pair(article["title"])
        translated_articles.append((article, title_fr, title_en))

    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.executemany(
            """
            INSERT OR IGNORE INTO daily_articles (
                url, title, title_fr, title_en, summary, published, source, article_day,
                category, country, asset_type, theme
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    article["url"],
                    article["title"],
                    title_fr,
                    title_en,
                    article["summary"],
                    article["published"],
                    article["source"],
                    article["article_day"],
                    classify_article(article["title"], article["summary"], article["source"]),
                    classify_country(article["title"], article["summary"], article["source"]),
                    classify_asset_type(article["title"], article["summary"]),
                    classify_theme(article["title"], article["summary"], article["source"]),
                )
                for article, title_fr, title_en in translated_articles
            ],
        )

    removed_count = 0
    for day in (
        (start_day + timedelta(days=offset)).isoformat()
        for offset in range((target_day - start_day).days + 1)
    ):
        removed_count += prune_daily_articles(day)

    with sqlite3.connect(DATABASE_PATH) as connection:
        stored_count = connection.execute(
            "SELECT COUNT(*) FROM daily_articles WHERE article_day BETWEEN ? AND ?",
            (start_day.isoformat(), target_day.isoformat()),
        ).fetchone()[0]

    if MAX_ARTICLES_PER_DAY <= 0:
        console_print(
            f"{stored_count} article(s) du {start_day.isoformat()} au {target_day.isoformat()} conservé(s) "
            "(sans plafond; fenêtre RSS: "
            f"{RSS_COLLECTION_WINDOW_DAYS} jours)."
        )
    else:
        console_print(
            f"{stored_count} article(s) du {start_day.isoformat()} au {target_day.isoformat()} conservé(s) "
            f"(plafond : {MAX_ARTICLES_PER_DAY}; fenêtre RSS: {RSS_COLLECTION_WINDOW_DAYS} jours)."
        )
    if removed_count:
        console_print(f"{removed_count} article(s) excédentaire(s) supprimé(s).")


def get_pending_daily_articles(article_day: str, theme: str | None = None) -> list[dict[str, str]]:
    anchor_day = date.fromisoformat(article_day)
    start_day = (anchor_day - timedelta(days=RSS_COLLECTION_WINDOW_DAYS - 1)).isoformat()

    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            """
            SELECT url, title, summary, published, source
            FROM daily_articles
            WHERE article_day BETWEEN ? AND ? AND score IS NULL
              AND (? IS NULL OR COALESCE(theme, 'finance') = ?)
            ORDER BY article_day DESC, published, url
            """,
            (start_day, article_day, theme, theme),
        ).fetchall()

    return [
        {
            "url": row[0],
            "title": row[1],
            "summary": row[2],
            "published": row[3] or "",
            "source": row[4],
        }
        for row in rows
    ]


def force_reanalyze_theme(article_day: str, theme: str) -> int:
    """Reset all scores for a specific theme so the day can be re-processed from scratch."""
    target_theme = (theme or "finance").lower()
    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.execute(
            "UPDATE daily_articles SET score = NULL, analyzed_at = NULL WHERE article_day = ? AND COALESCE(theme, 'finance') = ?",
            (article_day, target_theme),
        )
    return cursor.rowcount


def save_article(article: dict[str, str], content: str, score: float) -> None:
    analyzed_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            INSERT INTO articles (
                url, title, published, source, content, score, analyzed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article["url"],
                article["title"],
                article["published"],
                article["source"],
                content,
                score,
                analyzed_at,
            ),
        )


def save_daily_article(
    article: dict[str, str],
    content: str,
    score: float,
    impact_financier: float | None = None,
    confiance: float | None = None,
) -> None:
    analyzed_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            UPDATE daily_articles
            SET content = ?, score = ?, impact_financier = ?, confiance = ?, analyzed_at = ?
            WHERE url = ?
            """,
            (content, score, impact_financier, confiance, analyzed_at, article["url"]),
        )


def print_score_table(scores: list[dict[str, str | float]]) -> None:
    console_print("\nTableau des scores")
    console_print("=" * 80)
    console_print(f"{'Article':<68} | Score")
    console_print("-" * 80)

    for result in scores:
        title = result["title"]
        score = result["score"]
        console_print(f"{str(title)[:68]:<68} | {float(score):+.2f}")

    console_print("-" * 80)
    if scores:
        final_score = sum(float(result["score"]) for result in scores) / len(scores)
        console_print(f"Score final moyen : {final_score:+.2f}")
    else:
        console_print("Score final moyen : non disponible")


def process_new_articles(
    article_day: str | None = None,
    theme: str | None = None,
) -> tuple[int, list[dict[str, str | float]]]:
    article_day = article_day or datetime.now(timezone.utc).date().isoformat()
    processed_count = 0
    scores: list[dict[str, str | float]] = []
    batch_started_at = time.perf_counter()
    target_theme = (theme or "finance").lower()
    rss_urls = RSS_URLS if target_theme == "finance" else theme_rss_urls(target_theme)
    collect_daily_articles(article_day, rss_urls=rss_urls)
    pending_articles = get_pending_daily_articles(article_day, theme=target_theme)

    total_articles = len(pending_articles)
    if total_articles == 0:
        console_print("Aucun nouvel article à analyser.")
        console_print("Tous les articles disponibles ont déjà été traités.")
        return 0, []

    console_print(f"{total_articles} nouvel article à analyser.")
    console_print("Temps restant estimé : calcul après le premier article.")

    for article_number, article in enumerate(pending_articles, start=1):
        console_print(
            f"Article {article_number}/{len(pending_articles)} : "
            f"{article['title']}"
        )
        try:
            content = select_content(article)
            prompt_text = f"Titre : {article['title']}\n\nTexte : {content}"
            chunks = prepare_sentiment_chunks(prompt_text)
            if len(chunks) > 1:
                console_print(
                    f"Article long conservé et découpé en {len(chunks)} morceaux."
                )
            if os.getenv("SENTIMENT_PROVIDER", "local").lower() == "local":
                analysis = analyze_sentiment_local_details(prompt_text)
            else:
                score = analyze_sentiment(prompt_text)
                analysis = {"score": score, "impact_financier": None, "confiance": None}
            score = float(analysis["score"])
            save_daily_article(
                article,
                content,
                score,
                analysis.get("impact_financier"),
                analysis.get("confiance"),
            )
            processed_count += 1
            scores.append({"title": article["title"], "score": score})
            console_print(f"[{score:+.2f}] {article['title']}")

            remaining_count = total_articles - article_number
            if remaining_count:
                elapsed_seconds = time.perf_counter() - batch_started_at
                average_seconds = elapsed_seconds / processed_count
                estimated_seconds = (
                    average_seconds * remaining_count
                    + ARTICLE_DELAY_SECONDS * remaining_count
                )
                console_print(f"Articles restants : {remaining_count}")
                console_print(
                    "Temps restant estimé (limite non atteinte) : "
                    f"{format_duration(estimated_seconds)}"
                )
            else:
                console_print("Articles restants : 0")
                console_print("Temps restant estimé : 0 s")

            if article_number < total_articles and ARTICLE_DELAY_SECONDS > 0:
                console_print(
                    f"Pause de {ARTICLE_DELAY_SECONDS:g} secondes avant l'article suivant."
                )
                time.sleep(ARTICLE_DELAY_SECONDS)
        except QuotaExceededError as error:
            error.analyzed_count = processed_count
            error.remaining_count = len(pending_articles) - article_number + 1
            error.scores = scores
            raise
        except Exception as error:
            console_print(f"Erreur pour {article['url']} : {error}", error=True)
            console_print(
                f"Articles restant à analyser : {total_articles - article_number + 1}",
                error=True,
            )

    return processed_count, scores


def main() -> int:
    configure_output_encoding()

    parser = argparse.ArgumentParser(
        description="Analyse les nouveaux articles financiers depuis des flux RSS."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Effectue une collecte puis quitte.",
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="Date à traiter au format YYYY-MM-DD (défaut : aujourd'hui).",
    )
    parser.add_argument(
        "--theme",
        choices=("finance", "esg", "ecology"),
        default="finance",
        help="Famille thématique à collecter et analyser : finance, esg, ecology.",
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Réinitialise le score des articles du thème sélectionné pour un recalcul complet.",
    )
    arguments = parser.parse_args()

    initialize_database()
    if arguments.reanalyze:
        reset_count = force_reanalyze_theme(arguments.date, arguments.theme)
        console_print(f"{reset_count} article(s) remis en attente pour le thème {arguments.theme} le {arguments.date}.")

    while True:
        try:
            processed_count, scores = process_new_articles(arguments.date, theme=arguments.theme)
        except QuotaExceededError as error:
            console_print(str(error), error=True)
            console_print(f"Articles analysés : {error.analyzed_count}")
            console_print(
                "Articles restants non analysés à cause de la limite : "
                f"{error.remaining_count}"
            )
            print_score_table(error.scores)
            return 1

        console_print(f"{processed_count} nouvel article analysé.")
        print_score_table(scores)

        if arguments.once:
            return 0

        console_print(
            f"Prochaine vérification dans {POLL_INTERVAL_SECONDS} secondes."
        )
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
