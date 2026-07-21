#!/usr/bin/env python3

import logging
import os
import sys
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


TESLA_API_URL = (
    "https://www.tesla.com/inventory/api/v4/inventory-results"
)

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

TARGET_YEAR = 2024
MAX_PRICE_GBP = 26_000
REQUEST_TIMEOUT_SECONDS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

LOGGER = logging.getLogger(__name__)


class TrackerError(RuntimeError):
    pass


def get_required_environment_variable(name: str) -> str:
    value = os.environ.get(name)

    if not value or not value.strip():
        raise TrackerError(
            f"Required environment variable {name} is missing."
        )

    return value.strip()


def create_session() -> requests.Session:
    retry_strategy = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)

    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def parse_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} cannot be a boolean.")

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        cleaned_value = (
            value.replace("£", "")
            .replace(",", "")
            .replace(" ", "")
            .strip()
        )

        return int(cleaned_value)

    raise ValueError(
        f"Unable to convert {field_name}={value!r} to integer."
    )


def fetch_inventory(
    session: requests.Session,
) -> list[dict[str, Any]]:
    LOGGER.info("Requesting Tesla UK used Model Y inventory.")

    try:
        response = session.post(
            TESLA_API_URL,
            headers=TESLA_HEADERS,
            json=TESLA_PAYLOAD,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        LOGGER.info(
            "Tesla API returned HTTP status %s.",
            response.status_code,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        response_body = ""

        if exc.response is not None:
            response_body = exc.response.text[:500]

        raise TrackerError(
            "Tesla inventory request failed. "
            f"Details: {exc}. Response: {response_body}"
        ) from exc

    try:
        response_data = response.json()
    except requests.JSONDecodeError as exc:
        raise TrackerError(
            "Tesla response was not valid JSON. "
            f"Response preview: {response.text[:500]}"
        ) from exc

    if not isinstance(response_data, dict):
        raise TrackerError(
            "Tesla returned an unexpected response structure."
        )

    results = response_data.get("results")

    if not isinstance(results, list):
        raise TrackerError(
            'Tesla response did not contain a "results" array. '
            f"Available keys: {list(response_data.keys())}"
        )

    LOGGER.info(
        "Tesla returned %d vehicle result(s).",
        len(results),
    )

    return [
        vehicle
        for vehicle in results
        if isinstance(vehicle, dict)
    ]


def find_matching_vehicles(
    vehicles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []

    for vehicle in vehicles:
        try:
            year = parse_integer(vehicle.get("Year"), "Year")
            price = parse_integer(vehicle.get("Price"), "Price")
            vin = str(vehicle.get("VIN", "")).strip()

        except (TypeError, ValueError) as exc:
            LOGGER.warning(
                "Skipping vehicle with invalid data: %s",
                exc,
            )
            continue

        if not vin:
            LOGGER.warning("Skipping vehicle because VIN is missing.")
            continue

        if year == TARGET_YEAR and price <= MAX_PRICE_GBP:
            matches.append(
                {
                    "year": year,
                    "price": price,
                    "vin": vin,
                }
            )

    matches.sort(key=lambda vehicle: vehicle["price"])

    LOGGER.info(
        "Found %d matching vehicle(s).",
        len(matches),
    )

    return matches


def build_vehicle_url(vin: str) -> str:
    return (
        f"https://www.tesla.com/en_GB/my/order/{vin}"
        "?titleStatus=used&redirect=no#overview"
    )


def build_telegram_message(
    vehicle: dict[str, Any],
) -> str:
    vehicle_url = build_vehicle_url(vehicle["vin"])

    return (
        "🚨 *Tesla Inventory Alert*\n\n"
        f"*Year:* {vehicle['year']}\n"
        f"*Price:* £{vehicle['price']:,}\n"
        f"*VIN:* `{vehicle['vin']}`\n\n"
        f"[View Tesla listing]({vehicle_url})"
    )


def send_telegram_message(
    session: requests.Session,
    bot_token: str,
    chat_id: str,
    message: str,
) -> None:
    telegram_url = (
        f"https://api.telegram.org/bot{bot_token}/sendMessage"
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

        LOGGER.info(
            "Telegram API returned HTTP status %s.",
            response.status_code,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        response_body = ""

        if exc.response is not None:
            response_body = exc.response.text[:500]

        raise TrackerError(
            "Telegram notification failed. "
            f"Details: {exc}. Response: {response_body}"
        ) from exc

    result = response.json()

    if not result.get("ok"):
        raise TrackerError(
            f"Telegram rejected the message: {result}"
        )


def main() -> int:
    try:
        bot_token = get_required_environment_variable(
            "TELEGRAM_BOT_TOKEN"
        )

        chat_id = get_required_environment_variable(
            "TELEGRAM_CHAT_ID"
        )

        session = create_session()
        inventory = fetch_inventory(session)
        matches = find_matching_vehicles(inventory)

        if not matches:
            LOGGER.info(
                "No qualifying vehicles found. "
                "No Telegram notification sent."
            )
            return 0

        for vehicle in matches:
            send_telegram_message(
                session=session,
                bot_token=bot_token,
                chat_id=chat_id,
                message=build_telegram_message(vehicle),
            )

            LOGGER.info(
                "Telegram alert sent for VIN %s.",
                vehicle["vin"],
            )

        return 0

    except TrackerError as exc:
        LOGGER.error("%s", exc)
        return 1

    except Exception:
        LOGGER.exception("Unexpected tracker error.")
        return 1


if __name__ == "__main__":
    sys.exit(main())