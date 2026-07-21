from __future__ import annotations

import html
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger("tesla_tracker")

TESLA_API_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"
TELEGRAM_API_ROOT = "https://api.telegram.org"
STATE_FILE = Path(os.getenv("STATE_FILE", "inventory_state.json"))


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    model: str = "my"
    condition: str = "used"
    market: str = "GB"
    language: str = "en"
    locale: str = "en_GB"
    min_year: int = 2024
    max_price: int = 28_000
    trim_keywords: tuple[str, ...] = ("rear-wheel drive", "rwd")
    page_size: int = 50
    max_pages: int = 10
    timeout_seconds: int = 20

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        missing = [
            name
            for name, value in {
                "TELEGRAM_BOT_TOKEN": token,
                "TELEGRAM_CHAT_ID": chat_id,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        trim_keywords = tuple(
            keyword.strip().lower()
            for keyword in os.getenv(
                "TESLA_TRIM_KEYWORDS", "rear-wheel drive,rwd"
            ).split(",")
            if keyword.strip()
        )

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            min_year=int(os.getenv("TESLA_MIN_YEAR", "2024")),
            max_price=int(os.getenv("TESLA_MAX_PRICE", "28000")),
            trim_keywords=trim_keywords,
            page_size=int(os.getenv("TESLA_PAGE_SIZE", "50")),
            max_pages=int(os.getenv("TESLA_MAX_PAGES", "10")),
            timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
        )


@dataclass(frozen=True)
class Vehicle:
    vin: str
    price: int
    year: int
    model: str
    trim: str
    mileage: int | None
    location: str
    listing_url: str


@dataclass(frozen=True)
class Alert:
    kind: str
    vehicle: Vehicle
    previous_price: int | None = None


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
            ),
        }
    )
    return session


def make_inventory_query(settings: Settings, offset: int) -> dict[str, Any]:
    return {
        "query": {
            "model": settings.model,
            "condition": settings.condition,
            "options": {},
            "arrangeby": "Price",
            "order": "asc",
            "market": settings.market,
            "language": settings.language,
        },
        "offset": offset,
        "count": settings.page_size,
        "outsideOffset": 0,
        "isFalconDeliverySelectionEnabled": True,
        "version": "v2",
    }


def fetch_inventory(
    settings: Settings, session: requests.Session | None = None
) -> list[dict[str, Any]]:
    client = session or build_session()
    all_results: list[dict[str, Any]] = []

    for page_number in range(settings.max_pages):
        offset = page_number * settings.page_size
        query = make_inventory_query(settings, offset)
        LOGGER.info("Requesting Tesla inventory page %s", page_number + 1)

        response = client.get(
            TESLA_API_URL,
            params={"query": json.dumps(query, separators=(",", ":"))},
            timeout=settings.timeout_seconds,
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            preview = response.text[:250].replace("\n", " ")
            raise RuntimeError(
                f"Tesla returned a non-JSON response: {preview!r}"
            ) from exc

        results = payload.get("results")
        if not isinstance(results, list):
            raise RuntimeError(
                "Tesla response schema changed: expected a 'results' list"
            )

        all_results.extend(item for item in results if isinstance(item, dict))
        if len(results) < settings.page_size:
            break

    LOGGER.info("Retrieved %s raw Tesla inventory records", len(all_results))
    return all_results


def parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        cleaned = str(value).replace(",", "").replace("£", "").strip()
        return int(float(cleaned))
    except (TypeError, ValueError):
        return None


def first_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def searchable_vehicle_text(record: dict[str, Any]) -> str:
    useful_keys = (
        "TrimName",
        "TRIM",
        "Trim",
        "Title",
        "Model",
        "ModelVariant",
        "OptionCodeList",
        "OptionCodeData",
        "Badge",
    )
    values: list[str] = []
    for key in useful_keys:
        value = record.get(key)
        if value is not None:
            serialised = (
                json.dumps(value, ensure_ascii=False)
                if not isinstance(value, str)
                else value
            )
            values.append(serialised)
    return " ".join(values).lower()


def build_listing_url(vin: str, locale: str) -> str:
    return (
        f"https://www.tesla.com/{locale}/my/order/{vin}"
        "?titleStatus=used&redirect=no#overview"
    )


def parse_vehicle(record: dict[str, Any], settings: Settings) -> Vehicle | None:
    vin = first_text(record, "VIN", "Vin", "vin")
    price = parse_int(record.get("Price"))
    year = parse_int(record.get("Year"))

    if not vin or price is None or year is None:
        LOGGER.warning("Skipping malformed inventory record: %s", record)
        return None

    trim = first_text(record, "TrimName", "TRIM", "Trim", "ModelVariant")
    model = first_text(record, "Model", "ModelName", "Title") or "Model Y"
    mileage = parse_int(
        record.get("Odometer")
        or record.get("Mileage")
        or record.get("OdometerValue")
    )
    location = first_text(
        record, "MetroName", "City", "StateProvince", "Location", "Vrl"
    )

    return Vehicle(
        vin=vin,
        price=price,
        year=year,
        model=model,
        trim=trim,
        mileage=mileage,
        location=location,
        listing_url=build_listing_url(vin, settings.locale),
    )


def vehicle_matches(
    vehicle: Vehicle, raw_record: dict[str, Any], settings: Settings
) -> bool:
    if vehicle.year < settings.min_year or vehicle.price > settings.max_price:
        return False

    if not settings.trim_keywords:
        return True

    haystack = searchable_vehicle_text(raw_record)
    return any(keyword in haystack for keyword in settings.trim_keywords)


def filter_inventory(
    records: list[dict[str, Any]], settings: Settings
) -> list[Vehicle]:
    matches: list[Vehicle] = []
    seen_vins: set[str] = set()

    for record in records:
        vehicle = parse_vehicle(record, settings)
        if vehicle is None or vehicle.vin in seen_vins:
            continue
        seen_vins.add(vehicle.vin)
        if vehicle_matches(vehicle, record, settings):
            matches.append(vehicle)

    return sorted(matches, key=lambda vehicle: (vehicle.price, vehicle.year, vehicle.vin))


def load_state(path: Path = STATE_FILE) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read state file {path}") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(f"State file {path} must contain a JSON object")
    return loaded


def find_alerts(
    vehicles: list[Vehicle], previous_state: dict[str, dict[str, Any]]
) -> list[Alert]:
    alerts: list[Alert] = []
    for vehicle in vehicles:
        previous = previous_state.get(vehicle.vin)
        if previous is None:
            alerts.append(Alert(kind="new", vehicle=vehicle))
            continue

        previous_price = parse_int(previous.get("price"))
        if previous_price is not None and vehicle.price < previous_price:
            alerts.append(
                Alert(
                    kind="price_drop",
                    vehicle=vehicle,
                    previous_price=previous_price,
                )
            )
    return alerts


def save_state(
    vehicles: list[Vehicle],
    previous_state: dict[str, dict[str, Any]],
    path: Path = STATE_FILE,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    current_vins = {vehicle.vin for vehicle in vehicles}
    state = dict(previous_state)

    for vehicle in vehicles:
        original_first_seen = state.get(vehicle.vin, {}).get("first_seen", now)
        state[vehicle.vin] = {
            **asdict(vehicle),
            "first_seen": original_first_seen,
            "last_seen": now,
            "active": True,
        }

    for vin, entry in state.items():
        if vin not in current_vins and isinstance(entry, dict):
            entry["active"] = False

    path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def format_alert(alert: Alert) -> str:
    vehicle = alert.vehicle
    if alert.kind == "price_drop":
        heading = "TESLA PRICE DROP"
        price_line = (
            f"Price: <s>£{alert.previous_price:,}</s> → "
            f"<b>£{vehicle.price:,}</b>"
        )
    else:
        heading = "NEW TESLA MATCH"
        price_line = f"Price: <b>£{vehicle.price:,}</b>"

    trim = vehicle.trim or "Trim not supplied by Tesla"
    mileage_line = (
        f"\nMileage: {vehicle.mileage:,} miles"
        if vehicle.mileage is not None
        else ""
    )
    location_line = (
        f"\nLocation: {html.escape(vehicle.location)}" if vehicle.location else ""
    )

    return (
        f"<b>{heading}</b>\n\n"
        f"<b>{html.escape(str(vehicle.year))} "
        f"{html.escape(vehicle.model)} — {html.escape(trim)}</b>\n"
        f"{price_line}"
        f"{mileage_line}"
        f"{location_line}\n"
        f"VIN: <code>{html.escape(vehicle.vin)}</code>\n\n"
        f'<a href="{html.escape(vehicle.listing_url, quote=True)}">'
        "View Tesla listing</a>"
    )


def send_telegram_alert(
    alert: Alert,
    settings: Settings,
    session: requests.Session | None = None,
) -> None:
    client = session or build_session()
    url = f"{TELEGRAM_API_ROOT}/bot{settings.telegram_bot_token}/sendMessage"
    response = client.post(
        url,
        json={
            "chat_id": settings.telegram_chat_id,
            "text": format_alert(alert),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=settings.timeout_seconds,
    )
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError("Telegram returned a non-JSON response") from exc
    if result.get("ok") is not True:
        raise RuntimeError(f"Telegram rejected the alert: {result}")


def main() -> int:
    settings = Settings.from_environment()
    session = build_session()
    previous_state = load_state()
    records = fetch_inventory(settings, session=session)
    vehicles = filter_inventory(records, settings)
    alerts = find_alerts(vehicles, previous_state)

    LOGGER.info(
        "Found %s matching vehicles and %s alert-worthy changes",
        len(vehicles),
        len(alerts),
    )

    for alert in alerts:
        send_telegram_alert(alert, settings, session=session)
        LOGGER.info("Sent %s alert for VIN %s", alert.kind, alert.vehicle.vin)

    save_state(vehicles, previous_state)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        sys.exit(main())
    except Exception:
        LOGGER.exception("Tesla tracker failed")
        sys.exit(1)
