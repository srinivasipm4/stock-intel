
import os
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import requests
from google import genai
from google.oauth2.service_account import Credentials

# --------------------------------------------------
# Configuration
# --------------------------------------------------

SHEET_ID = "1O6hiefoF3IlStKFJrC7RD827GsOC5asWbEht6Sw_SC8"

WATCHLIST_TAB = "Watchlist"
MAIN_WATCHLIST_TAB = "MainWatchlist"
VOLUME_TAB = "Volume_History"
ALERT_LOG_TAB = "Alert_Log"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

ALERT_API_SECRET = os.environ["ALERT_API_SECRET"]
ALERT_WEB_APP_URL = os.environ["ALERT_WEB_APP_URL"]

IST = ZoneInfo("Asia/Kolkata")

THRESHOLDS = [
    (-2.0, "-2%"),
    (-5.0, "-5%"),
    (-10.0, "-10%"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# --------------------------------------------------
# Google Sheets connection
# --------------------------------------------------

def connect_to_spreadsheet():
    credentials_info = json.loads(SERVICE_ACCOUNT_JSON)

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly"
        ]
    )

    client = gspread.authorize(credentials)
    return client.open_by_key(SHEET_ID)


# --------------------------------------------------
# Read percentage change
# --------------------------------------------------

def parse_percentage(value):
    if value is None or str(value).strip() == "":
        return None

    text = str(value).strip()
    text = text.replace("−", "-").replace(",", "")

    has_percent_sign = "%" in text
    text = text.replace("%", "").strip()

    try:
        number = float(text)
    except ValueError:
        return None

    # Handle an unformatted decimal percentage such as -0.024.
    if not has_percent_sign and -1 < number < 1:
        number *= 100

    return number


# --------------------------------------------------
# Read volume history and calculate indicators
# --------------------------------------------------

def calculate_volume_metrics(volume_rows, stock):
    records = []

    for row in volume_rows:
        if len(row) < 3:
            continue

        date_text = str(row[0]).strip()
        stock_code = str(row[1]).strip()

        if stock_code != stock:
            continue

        match = re.search(r"\d{4}-\d{2}-\d{2}", date_text)

        if not match:
            continue

        try:
            volume = float(str(row[2]).replace(",", ""))
        except (ValueError, TypeError):
            continue

        records.append((match.group(0), volume))

    # Keep one record per date, then sort chronologically.
    by_date = {date: volume for date, volume in records}
    records = sorted(by_date.items(), key=lambda item: item[0])

    if len(records) < 2:
        return None, None

    latest_volume = records[-1][1]
    previous_volume = records[-2][1]

    day_change = None
    if previous_volume > 0:
        day_change = (
            (latest_volume - previous_volume)
            / previous_volume
        ) * 100

    average_multiple = None

    # Compare the latest session with the previous 20 sessions.
    previous_20 = [volume for _, volume in records[-21:-1]]

    if len(previous_20) == 20:
        average_volume = sum(previous_20) / 20

        if average_volume > 0:
            average_multiple = latest_volume / average_volume

    return day_change, average_multiple


# --------------------------------------------------
# Google News RSS collection
# --------------------------------------------------

def collect_google_news_rss():
    feed_url = "https://news.google.com/rss/search"

    params = {
        "q": "Indian stock market OR NSE OR BSE OR Nifty OR Sensex when:1d",
        "hl": "en-IN",
        "gl": "IN",
        "ceid": "IN:en"
    }

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(
        feed_url,
        params=params,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    root = ET.fromstring(response.content)
    news_items = []

    for item in root.findall(".//item"):
        source_element = item.find("source")

        news_items.append({
            "title": item.findtext("title", default="").strip(),
            "link": item.findtext("link", default="").strip(),
            "published": item.findtext("pubDate", default="").strip(),
            "source": (
                source_element.text.strip()
                if source_element is not None and source_element.text
                else "Google News"
            )
        })

    logging.info(
        "Google News RSS collected %s headlines",
        len(news_items)
    )

    return news_items


# --------------------------------------------------
# NSE RSS feed configuration
# --------------------------------------------------

NSE_RSS_FEEDS = {
    "NSE Corporate Announcements":
        "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",

    "NSE Financial Results":
        "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml",

    "NSE Board Meetings":
        "https://nsearchives.nseindia.com/content/RSS/Board_Meetings.xml",

    "NSE Corporate Actions":
        "https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml",

    "NSE Insider Trading":
        "https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml",

    "NSE Shareholding Patterns":
        "https://nsearchives.nseindia.com/content/RSS/Shareholding_Pattern.xml",

    "NSE Circulars":
        "https://nsearchives.nseindia.com/content/RSS/Circulars.xml",

    "NSE Annual Reports":
        "https://nsearchives.nseindia.com/content/RSS/Annual_Reports.xml"
}


# --------------------------------------------------
# BSE RSS feed configuration
# --------------------------------------------------

BSE_RSS_FEEDS = {
    "BSE Corporate Announcements":
        "https://www.bseindia.com/data/xml/announcements.aspx",

    "BSE Financial Results":
        "https://www.bseindia.com/Data/XML/FinancialResultsFeed.aspx",

    "BSE Board Meetings":
        "https://beta.bseindia.com/Data/XML/BoardMeetingsFeed.xml",

    "BSE Corporate Actions":
        "https://www.bseindia.com/data/XML/CorpActionFeed.aspx",

    "BSE Insider Trading":
        "https://www.bseindia.com/Data/XML/InsiderTradingFeed.aspx",

    "BSE Shareholding Patterns":
        "https://www.bseindia.com/Data/XML/ShareholdingPattern_Feed.aspx",
    
    "BSE Notices":
        "https://www.bseindia.com/data/xml/notices.xml"
}


def collect_marketaux_news():
    """Fetch a small batch of financial news from Marketaux."""
    api_key = os.getenv("MARKETAUX_API_KEY")

    if not api_key:
        logging.warning("MARKETAUX_API_KEY is not configured.")
        return []

    url = "https://api.marketaux.com/v1/news/all"

    params = {
        "api_token": api_key,
        "language": "en",
        "limit": 3,
        
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=20
        )
        response.raise_for_status()

        payload = response.json()
        articles = payload.get("data", [])
        all_news = []

        for article in articles:
            title = (article.get("title") or "").strip()
            link = (article.get("url") or "").strip()

            if not title or not link:
                continue

            entities = article.get("entities") or []

            matched_entities = []
            for entity in entities:
                matched_entities.append({
                    "name": entity.get("name", ""),
                    "symbol": entity.get("symbol", ""),
                    "exchange": entity.get("exchange", "")
                })

            all_news.append({
                "title": title,
                "link": link,
                "source": article.get("source", "Marketaux"),
                "published": article.get("published_at", ""),
                "entities": matched_entities
            })

        logging.info(
            f"Marketaux returned {len(all_news)} financial news articles."
        )
        return all_news

    except Exception as e:
        logging.warning(f"Marketaux news collection failed: {e}")
        return []


def collect_media_rss():
    """Collect public headlines from Business Standard and Mint RSS feeds."""
    all_news = []

    feeds = {
        "Business Standard Markets":
            "https://www.business-standard.com/rss/markets-106.rss",
        "Business Standard India":
            "https://www.business-standard.com/rss/india-news-216.rss",
        "Business Standard Companies":
            "https://www.business-standard.com/rss/companies-101.rss",
        "Mint Markets":
            "https://www.livemint.com/rss/marketsRSS",
        "Mint Companies":
            "https://www.livemint.com/rss/companiesRSS",
        "Mint Industry":
            "https://www.livemint.com/rss/industryRSS"
    }

    for source, feed_url in feeds.items():
        try:
            response = requests.get(
                feed_url,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            response.raise_for_status()

            root = ET.fromstring(response.content)

            for item in root.findall(".//item"):
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                published = item.findtext("pubDate", "").strip()

                if title and link:
                    all_news.append({
                        "title": title,
                        "link": link,
                        "source": source,
                        "published": published
                    })

        except Exception as e:
            logging.warning(
                f"Could not collect {source} RSS: {e}"
            )

    logging.info(
        f"Collected {len(all_news)} headlines from media RSS feeds."
    )
    return all_news


# --------------------------------------------------
# Common NSE and BSE RSS feed collector
# --------------------------------------------------

def collect_exchange_rss():
    all_news = []

    feeds = {
        **NSE_RSS_FEEDS,
        **BSE_RSS_FEEDS
    }

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    for feed_name, feed_url in feeds.items():
        try:
            response = requests.get(
                feed_url,
                headers=headers,
                timeout=30
            )

            response.raise_for_status()

            root = ET.fromstring(response.content)

            items = root.findall(".//item")

            for item in items:
                title = item.findtext("title", default="").strip()
                link = item.findtext("link", default="").strip()
                published = item.findtext("pubDate", default="").strip()

                if not title:
                    continue

                all_news.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": feed_name
                })

            logging.info(
                "%s: Retrieved %s items",
                feed_name,
                len(items)
            )

        except Exception as error:
            logging.warning(
                "%s feed failed: %s",
                feed_name,
                error
            )

    logging.info(
        "Total NSE and BSE RSS items collected: %s",
        len(all_news)
    )

    return all_news

def analyze_stock_drop(
    stock_name,
    stock_code,
    company_name,
    aliases,
    price_change,
    news_items
):
    """Analyse a stock decline using stock-specific news and AI fallbacks."""

    import re

    if not news_items:
        return "No relevant news found. No clear explanation identified."

    def normalise(value):
        return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()

    company_normalised = normalise(company_name)
    stock_name_normalised = normalise(stock_name)
    code_normalised = normalise(stock_code)

    alias_list = [
    normalise(alias)
    for alias in str(aliases or "").split(",")
    if normalise(alias)
    ]

    identity_names = [
        name for name in [
            company_normalised,
            stock_name_normalised,
            *alias_list
        ]
        if name
    ]

    
    # Remove exchange prefixes such as NSE or BSE.
    code_parts = code_normalised.split()
    ticker = code_parts[-1] if code_parts else ""

    # Avoid generic company-name words causing false matches.
    # Avoid generic company-name words causing false matches.
    generic_words = {
        "limited", "ltd", "industries", "industry", "company",
        "corporation", "corporate", "enterprises", "enterprise",
        "india", "indian", "group", "services", "service",
        "holdings", "holding", "private", "public"
    }

    name_terms = {
        word
        for name in identity_names
        for word in name.split()
        if len(word) >= 5 and word not in generic_words
    }
    
    matched_articles = []

    for item in news_items:
        title = str(item.get("title", ""))
        source = str(item.get("source", ""))
        link = str(item.get("link", ""))

        searchable = normalise(" ".join([
            title,
            str(item.get("description", "")),
            str(item.get("summary", "")),
            str(item.get("content", "")),
            str(item.get("entities", "")),
            str(item.get("matched_entities", "")),
            link
        ]))

        score = 0

        # Match the full legal name, stock name, or any alias.
        for identity_name in identity_names:
            if re.search(
                r"\b" + re.escape(identity_name) + r"\b",
                searchable
            ):
                score += 12
                break
        # Exact ticker match as a separate word.
        if ticker and len(ticker) >= 3:
            if re.search(r"\b" + re.escape(ticker) + r"\b", searchable):
                score += 8

        # Distinctive company-name terms.
        # Give supporting weight when at least two distinctive
        # company-name words appear in the article.
        matched_terms = {
            term for term in name_terms
            if re.search(r"\b" + re.escape(term) + r"\b", searchable)
        }

        if len(matched_terms) >= 2:
            score += min(6, len(matched_terms) * 2)

        if score > 0:
            matched_articles.append((score, item))

    if not matched_articles:
        logging.info(
            "No stock-specific news matched for %s (%s)",
            stock_name, stock_code
        )
        return "No stock-specific news found. No clear explanation identified."

    # Highest relevance first; limit the AI request to eight articles.
    matched_articles.sort(key=lambda entry: entry[0], reverse=True)
    relevant_news = [item for _, item in matched_articles[:8]]

    logging.info(
        "Selected %s stock-specific articles from %s collected articles for %s",
        len(relevant_news), len(news_items), stock_code
    )

    news_text = "\n\n".join(
        f"Headline: {str(item.get('title', ''))[:500]}\n"
        f"Source: {str(item.get('source', ''))[:150]}\n"
        f"Date: {str(item.get('published', ''))[:80]}\n"
        f"Link: {str(item.get('link', ''))[:500]}"
        for item in relevant_news
    )

    prompt = f"""
You are an Indian stock market news analyst.

Stock name: {stock_name}
Legal company name: {company_name}
Other names / aliases: {aliases}
Stock code: {stock_code}
Today's price change: {price_change:.2f}%

The following articles were selected because they appear relevant
to this company. Relevance does not prove causation.

{news_text}

Produce a concise analysis with these sections:

1. CONFIRMED DEVELOPMENTS:
Include only developments explicitly supported by an identifiable
official source, such as NSE, BSE, SEBI, RBI, PIB, a government
department, a court, or a company filing.
Include the source name and link.
If none qualify, write "None identified".

2. POSSIBLE EXPLANATIONS:
List plausible explanations suggested by the supplied reports.
Clearly label them as unconfirmed possibilities.
Include source names and links.
Do not claim they caused the price fall.

3. OVERALL ASSESSMENT:
Choose one:
- Official development identified
- Possible explanation only
- No clear explanation found

Never invent facts, sources, links, or causal relationships.
Do not treat an article headline alone as proof of causation.
If the supplied articles do not support an explanation, say so.
Keep the response concise.
"""

    # Provider 1: Gemini
    api_key = os.getenv("GEMINI_API_KEY")

    if api_key:
        try:
            client = genai.Client(
                api_key=api_key,
                http_options=genai.types.HttpOptions(timeout=30000)
            )

            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt
            )

            result = (response.text or "").strip()

            if result:
                logging.info("Stock-drop analysis completed using Gemini")
                return result

            logging.warning("Gemini returned an empty response")

        except Exception:
            logging.exception("Gemini stock-drop analysis failed")
    else:
        logging.warning("Gemini API key is not configured")

    # Provider 2: Groq
    groq_key = os.getenv("GROQ_API_KEY")

    if groq_key:
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openai/gpt-oss-20b",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 700
                },
                timeout=30
            )
            response.raise_for_status()

            result = response.json()["choices"][0]["message"]["content"]

            if result and result.strip():
                logging.info("Stock-drop analysis completed using Groq")
                return "🟡 Backup AI: Groq\n" + result.strip()

        except Exception:
            logging.exception("Groq backup failed")
    else:
        logging.warning("Groq API key is not configured")

    # Provider 3: OpenRouter
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    if openrouter_key:
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openrouter/free",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 700
                },
                timeout=30
            )
            response.raise_for_status()

            result = response.json()["choices"][0]["message"]["content"]

            if result and result.strip():
                logging.info("Stock-drop analysis completed using OpenRouter")
                return "🟡 Backup AI: OpenRouter\n" + result.strip()

        except Exception:
            logging.exception("OpenRouter backup failed")
    else:
        logging.warning("OpenRouter API key is not configured")

    logging.error("All three AI providers failed")
    return "AI analysis unavailable. No cause has been verified."

# --------------------------------------------------
# Telegram notification
# --------------------------------------------------

def send_telegram_message(message):
    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError("Telegram did not confirm message delivery.")


# --------------------------------------------------
# Read existing alert records
# --------------------------------------------------

def read_existing_alerts(alert_rows):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    sent = set()

    for row in alert_rows[1:]:
        if len(row) < 3:
            continue

        date_text = str(row[0]).strip()
        stock = str(row[1]).strip()
        level = str(row[2]).strip()

        match = re.search(r"\d{4}-\d{2}-\d{2}", date_text)

        if match and match.group(0) == today:
            sent.add((stock, level))

    return today, sent


# --------------------------------------------------
# Record alert through Apps Script
# --------------------------------------------------

def record_alert(date, stock, level, price_change):
    response = requests.post(
        ALERT_WEB_APP_URL,
        json={
            "secret": ALERT_API_SECRET,
            "date": date,
            "stock": stock,
            "alertLevel": level,
            "priceChange": price_change
        },
        timeout=30,
        allow_redirects=True
    )

    response.raise_for_status()

    try:
        result = response.json()
    except ValueError as error:
        raise RuntimeError(
            "Alert logger returned an unreadable response."
        ) from error

    if not result.get("success"):
        raise RuntimeError(
            f"Alert logger rejected the record: {result}"
        )

    logging.info(
        "Alert log recorded: %s %s (duplicate=%s)",
        stock,
        level,
        result.get("duplicate", False)
    )


# --------------------------------------------------
# Main alert engine
# --------------------------------------------------

def main():
    logging.info("Starting Indian Stock Intelligence Agent")

    spreadsheet = connect_to_spreadsheet()

    watchlist = spreadsheet.worksheet(
        WATCHLIST_TAB
    ).get_all_records()

    main_watchlist = spreadsheet.worksheet(
        MAIN_WATCHLIST_TAB
    ).get_all_records()
    
    logging.info(
        "Read %s stocks from MainWatchlist for news intelligence",
        len(main_watchlist)
    )

    volume_rows = spreadsheet.worksheet(
        VOLUME_TAB
    ).get_all_values()

    alert_rows = spreadsheet.worksheet(
        ALERT_LOG_TAB
    ).get_all_values()

    today, already_sent = read_existing_alerts(alert_rows)

    logging.info(
        "Read %s watchlist rows and %s volume rows",
        len(watchlist),
        max(0, len(volume_rows) - 1)
    )

    alerts_sent = 0
    alert_news = None
    
    for stock_row in watchlist:
        stock = str(stock_row.get("CODE", "")).strip()
        stock_name = str(stock_row.get("STOCK", stock)).strip()

        if not stock:
            continue

        price_change = parse_percentage(
            stock_row.get("Change")
        )

        if price_change is None:
            logging.warning(
                "Could not read percentage change for %s",
                stock
            )
            continue

        # Find all newly breached thresholds.
        new_levels = [
            (threshold, level)
            for threshold, level in THRESHOLDS
            if price_change <= threshold
            and (stock, level) not in already_sent
        ]

        if not new_levels:
            continue
                # Fetch news once per run, only when a new alert exists.
        if alert_news is None:
            alert_news = []

            news_collectors = [
                collect_google_news_rss,
                collect_exchange_rss,
                collect_media_rss,
                collect_marketaux_news
            ]

            for collector in news_collectors:
                try:
                    alert_news.extend(collector())
                except Exception:
                    logging.exception(
                        "News collection failed: %s",
                        collector.__name__
                    )

            logging.info(
                "Collected %s news articles for price-alert analysis",
                len(alert_news)
            )
        # Send one consolidated message if several levels
        # were crossed between scheduled scans.
        breached_levels = [
            level for _, level in new_levels
        ]

        day_volume_change, average_multiple = (
            calculate_volume_metrics(volume_rows, stock)
        )
        
        company_name = str(
            stock_row.get("COMPANY", "")
        ).strip()

        aliases = str(
            stock_row.get("ALIASES", "")
        ).strip()

        drop_analysis = analyze_stock_drop(
            stock_name,
            stock,
            company_name,
            aliases,
            price_change,
            alert_news or []
        )
        
        message = (
            "🚨 INDIAN STOCK PRICE ALERT\n\n"
            f"Stock: {stock_name}\n"
            f"Code: {stock}\n"
            f"Today's price change: {price_change:.2f}%\n"
            f"New threshold(s) crossed: {', '.join(breached_levels)}\n"
        )

        if day_volume_change is not None:
            message += (
                f"\nVolume vs previous session: "
                f"{day_volume_change:+.1f}%"
            )
        else:
            message += (
                "\nVolume vs previous session: Not available"
            )

        if average_multiple is not None:
            message += (
                f"\nVolume vs 20-day average: "
                f"{average_multiple:.2f}x"
            )
        else:
            message += (
                "\nVolume vs 20-day average: "
                "Not enough history"
            )
        message += (
            "\n\n📰 AI NEWS ANALYSIS\n"
            f"{drop_analysis}"
        )
        # Send Telegram first. Only record alerts after
        # Telegram confirms successful delivery.
        send_telegram_message(message)

        for _, level in new_levels:
            record_alert(
                today,
                stock,
                level,
                price_change
            )
            already_sent.add((stock, level))

        alerts_sent += 1

        logging.info(
            "Alert sent for %s at %s",
            stock,
            price_change
        )

    
    logging.info(
        "Agent completed. Notifications sent: %s",
        alerts_sent
    )


if __name__ == "__main__":
    main()
