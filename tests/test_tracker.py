from pathlib import Path

import pytest

from tracker import (
    Alert,
    Settings,
    Vehicle,
    filter_inventory,
    find_alerts,
    format_alert,
    load_state,
    save_state,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="token",
        telegram_chat_id="chat",
        min_year=2024,
        max_price=28_000,
        trim_keywords=("rear-wheel drive", "rwd"),
    )


def test_filter_inventory_returns_only_matching_rwd(settings: Settings) -> None:
    records = [
        {
            "VIN": "VIN-RWD",
            "Price": "27,990",
            "Year": "2024",
            "Model": "Model Y",
            "TrimName": "Rear-Wheel Drive",
            "Odometer": "10000",
        },
        {
            "VIN": "VIN-AWD",
            "Price": 27_500,
            "Year": 2024,
            "Model": "Model Y",
            "TrimName": "Long Range All-Wheel Drive",
        },
        {
            "VIN": "VIN-EXPENSIVE",
            "Price": 29_000,
            "Year": 2024,
            "TrimName": "RWD",
        },
    ]

    result = filter_inventory(records, settings)

    assert [vehicle.vin for vehicle in result] == ["VIN-RWD"]
    assert result[0].price == 27_990


def test_find_alerts_detects_new_and_price_drop() -> None:
    vehicles = [
        Vehicle("NEW", 27_000, 2024, "Model Y", "RWD", None, "", "url"),
        Vehicle("DROP", 26_000, 2024, "Model Y", "RWD", None, "", "url"),
        Vehicle("SAME", 25_000, 2024, "Model Y", "RWD", None, "", "url"),
    ]
    previous = {
        "DROP": {"price": 27_000},
        "SAME": {"price": 25_000},
    }

    alerts = find_alerts(vehicles, previous)

    assert [(alert.kind, alert.vehicle.vin) for alert in alerts] == [
        ("new", "NEW"),
        ("price_drop", "DROP"),
    ]
    assert alerts[1].previous_price == 27_000


def test_state_round_trip_marks_missing_vehicle_inactive(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    previous = {"OLD": {"price": 30_000, "active": True}}
    current = [Vehicle("NEW", 27_000, 2024, "Model Y", "RWD", None, "", "url")]

    save_state(current, previous, path)
    loaded = load_state(path)

    assert loaded["NEW"]["active"] is True
    assert loaded["OLD"]["active"] is False


def test_format_price_drop_alert() -> None:
    vehicle = Vehicle(
        "VIN123",
        26_500,
        2024,
        "Model Y",
        "Rear-Wheel Drive",
        9_000,
        "Manchester",
        "https://example.com",
    )
    message = format_alert(Alert("price_drop", vehicle, previous_price=27_500))

    assert "TESLA PRICE DROP" in message
    assert "£27,500" in message
    assert "£26,500" in message
    assert "9,000 miles" in message
