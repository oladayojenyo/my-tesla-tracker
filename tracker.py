#!/usr/bin/env python3
"""
Tesla UK Approved Used Inventory Tracker.

Queries Tesla's public inventory endpoint for used Model Y vehicles and sends
Telegram notifications for matching vehicles.

Required environment variables:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


TESLA_INVENTORY_URL = (
    "https://www.tesla.com/inventory/api/v4/inventory-results"
)

TELEGRAM_API_BASE_URL = "https://api.telegram.org"

TARGET_YEAR = 2024
MAX_PRICE_GBP = 26_000
REQUEST_TIMEOUT_SECONDS = 30

TESLA_PAYLOAD: dict[str, Any] = {
    "query": {
        "model": "my",
        "condition": "used",
        "options": {},
        "arrangeby": "Price",
        "order": "asc",
        "market": "GB",
        "language": "en",
    },
    "offset": 0,
    "count": 50,
}

TESLA_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.tesla.com",
    "Referer": "https://www.tesla.com/en_GB/inventory/used/my",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger(__name__)


class TrackerError(RuntimeError):
    """Raised when the tracker cannot complete successfully."""


def require_environment_variable(name: str) -> str:
    """
    Return a required environment variable.

    Raises:
        TrackerError: If the variable is missing or empty.
    """
    value = os.environ.get(name)

    if value is None or not value.strip():
        raise TrackerError(
            f"Required environment variable {name!r} is not configured."
        )

    return value.strip()


def create_http_session() -> requests.Session:
    """
    Create an HTTP session with retries for temporary network or server errors.
    """
    retry_policy = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry_policy,
        pool_connections=5,
        pool_maxsize=5,
    )

    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def parse_integer(value: Any, field_name: str) -> int:
    """
    Convert a Tesla API value into an integer.

    Handles integer values and strings containing commas or currency symbols,
    such as "26,000" or "£26,000".
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} cannot be a boolean.")

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        cleaned_value = re.sub(r"[^\d-]", "", value)

        if cleaned_value and cleaned_value != "-":
            return int(cleaned_value)

    raise ValueError(
        f"Could not convert {field_name} value {value!r} to an integer."
    )


def fetch_inventory(session: requests.Session) -> list[dict[str, Any]]:
    """
    Retrieve Tesla's UK used Model Y inventory.

    Returns:
        A list of vehicle dictionaries from the response's "results" key.
    """
    LOGGER.info("Querying Tesla UK used Model Y inventory.")

    try:
        response = session.post(
            TESLA_INVENTORY_URL,
            headers=TESLA_HEADERS,
            json=TESLA_PAYLOAD,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TrackerError(
            f"Tesla inventory request failed: {exc}"
        ) from exc

    try:
        response_data = response.json()
    except requests.JSONDecodeError as exc:
        response_preview = response.text[:500].replace("\n", " ")
        raise TrackerError(
            "Tesla returned a response that was not valid JSON. "
            f"Response preview: {response_preview!r}"
        ) from exc

    if not isinstance(response_data, dict):
        raise TrackerError(
            "Tesla returned an unexpected top-level JSON structure."
        )

    results = response_data.get("results")

    if not isinstance(results, list):
        raise TrackerError(
            'Tesla response did not contain a valid "results" array.'
        )

    vehicles = [
        vehicle
        for vehicle in results
        if isinstance(vehicle, dict)
    ]

    LOGGER.info(
        "Tesla returned %d inventory result(s).",
        len(vehicles),
    )

    return vehicles


def extract_matching_vehicles(
    vehicles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Extract and filter vehicles matching the configured year and price rules.
    """
    matches: list[dict[str, Any]] = []

    for vehicle in vehicles:
        try:
            price = parse_integer(vehicle.get("Price"), "Price")
            year = parse_integer(vehicle.get("Year"), "Year")
            vin = str(vehicle.get("VIN", "")).strip()
        except (TypeError, ValueError) as exc:
            LOGGER.warning(
                "Skipping vehicle with invalid data: %s. Vehicle: %r",
                exc,
                vehicle,
            )
            continue

        if not vin:
            LOGGER.warning(
                "Skipping vehicle because its VIN is missing: %r",
                vehicle,
            )
            continue

        if year == TARGET_YEAR and price <= MAX_PRICE_GBP:
            matches.append(
                {
                    "Price": price,
                    "Year": year,
                    "VIN": vin,
                }
            )

    matches.sort(key=lambda item: item["Price"])

    LOGGER.info(
        "Found %d vehicle(s) matching Year == %d and Price <= £%s.",
        len(matches),
        TARGET_YEAR,
        f"{MAX_PRICE_GBP:,}",
    )

    return matches


def build_vehicle_url(vin: str) -> str:
    """
    Construct the direct Tesla UK used-vehicle buying URL.

    Tesla inventory responses normally expose VIN as a bare VIN rather than a
    complete URL path, so the UK Model Y order path is constructed explicitly.
    """
    encoded_vin = quote(vin, safe="")

    return (
        f"https://www.tesla.com/en_GB/my/order/{encoded_vin}"
        "?titleStatus=used&redirect=no#overview"
    )


def build_telegram_message(vehicle: dict[str, Any]) -> str:
    """
    Build a Telegram Markdown notification for one matching vehicle.
    """
    price = vehicle["Price"]
    year = vehicle["Year"]
    vin = vehicle["VIN"]
    vehicle_url = build_vehicle_url(vin)

    return (
        "🚨 *Tesla Inventory Alert*\n\n"
        f"*Year:* {year}\n"
        f"*Price:* £{price:,}\n"
        f"*VIN:* `{vin}`\n\n"
        f"[View and buy this Tesla]({vehicle_url})"
    )


def send_telegram_message(
    session: requests.Session,
    bot_token: str,
    chat_id: str,
    message: str,
) -> None:
    """
    Send one Markdown-formatted message through the Telegram Bot API.
    """
    telegram_url = (
        f"{TELEGRAM_API_BASE_URL}/bot{bot_token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        response = session.post(
            telegram_url,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TrackerError(
            f"Telegram notification request failed: {exc}"
        ) from exc

    try:
        response_data = response.json()
    except requests.JSONDecodeError as exc:
        raise TrackerError(
            "Telegram returned a response that was not valid JSON."
        ) from exc

    if not response_data.get("ok"):
        raise TrackerError(
            "Telegram rejected the notification: "
            f"{response_data!r}"
        )


def run() -> int:
    """
    Run the complete inventory-checking process.

    Returns:
        A process exit code.
    """
    try:
        bot_token = require_environment_variable(
            "TELEGRAM_BOT_TOKEN"
        )
        chat_id = require_environment_variable(
            "TELEGRAM_CHAT_ID"
        )

        session = create_http_session()
        vehicles = fetch_inventory(session)
        matches = extract_matching_vehicles(vehicles)

        if not matches:
            LOGGER.info(
                "No qualifying vehicles found. "
                "No Telegram notification was sent."
            )
            return 0

        for vehicle in matches:
            message = build_telegram_message(vehicle)

            send_telegram_message(
                session=session,
                bot_token=bot_token,
                chat_id=chat_id,
                message=message,
            )

            LOGGER.info(
                "Telegram notification sent for VIN %s at £%s.",
                vehicle["VIN"],
                f"{vehicle['Price']:,}",
            )

        return 0

    except TrackerError as exc:
        LOGGER.error("%s", exc)
        return 1
    except Exception:
        LOGGER.exception("Unexpected tracker failure.")
        return 1


if __name__ == "__main__":
    sys.exit(run())