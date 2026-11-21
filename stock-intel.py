
import os
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials

# --------------------------------------------------
# Configuration
# --------------------------------------------------

SHEET_ID = "1O6hiefoF3IlStKFJrC7RD827GsOC5asWbEht6Sw_SC8"

WATCHLIST_TAB = "Watchlist"
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

        # Send one consolidated message if several levels
        # were crossed between scheduled scans.
        breached_levels = [
            level for _, level in new_levels
        ]

        day_volume_change, average_multiple = (
            calculate_volume_metrics(volume_rows, stock)
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

     # Combined RSS connectivity test

    all_news = []

    try:
        google_news = collect_google_news_rss()
        all_news.extend(google_news)

        logging.info(
            "Google News RSS: Retrieved %s headlines",
            len(google_news)
        )

    except Exception:
        logging.exception("Google News RSS test failed")

    try:
        exchange_news = collect_exchange_rss()
        all_news.extend(exchange_news)

        logging.info(
            "NSE/BSE RSS: Retrieved %s headlines",
            len(exchange_news)
        )

    except Exception:
        logging.exception("NSE/BSE RSS test failed")

    logging.info(
        "COMBINED RSS TEST: Retrieved %s total headlines",
        len(all_news)
    )

    for item in all_news[:15]:
        logging.info(
            "RSS HEADLINE: %s | SOURCE: %s | DATE: %s | LINK: %s",
            item["title"],
            item["source"],
            item["published"],
            item["link"]
        )

    logging.info(
        "Agent completed. Notifications sent: %s",
        alerts_sent
    )


if __name__ == "__main__":
    main()
