
import os
import json
import logging
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials
import requests

# --------------------------------------------------
# Configuration
# --------------------------------------------------

SHEET_ID = "1O6hiefoF3IlStKFJrC7RD827GsOC5asWbEht6Sw_SC8"
WORKSHEET_NAME = "Watchlist"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# --------------------------------------------------
# Google Sheets connection
# --------------------------------------------------

def connect_to_sheet():
    credentials_info = json.loads(SERVICE_ACCOUNT_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ]

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=scopes
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(SHEET_ID)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)

    return worksheet


# --------------------------------------------------
# Telegram notification
# --------------------------------------------------

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()


# --------------------------------------------------
# Read watchlist
# --------------------------------------------------

def read_watchlist():
    worksheet = connect_to_sheet()
    rows = worksheet.get_all_records()

    logging.info("Read %s stock rows from Google Sheet", len(rows))

    return rows


# --------------------------------------------------
# Initial connection test
# --------------------------------------------------

def main():
    logging.info("Starting Indian Stock Intelligence Agent")

    stocks = read_watchlist()

    message = (
        "✅ Stock Intelligence Agent connected!\n\n"
        f"Watchlist: {len(stocks)} stocks\n"
        f"Sheet tab: {WORKSHEET_NAME}\n"
        f"Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "Google Sheets connection successful."
    )

    send_telegram_message(message)

    logging.info("Telegram test message sent successfully")


if __name__ == "__main__":
    main()
