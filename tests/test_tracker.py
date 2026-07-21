from __future__ import annotations

import json

import pytest

import tracker


# ---------------------------------------------------------------------------
# parse_int
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        (28000, 28000),
        ("28000", 28000),
        ("£28,000", 28000),
        ("28,500.00", 28500),
        (None, None),
        (True, None),
        (False, None),
        ("not a number", None),
        ("", None),
    ],
)
def test_parse_int(value, expected):
    assert tracker.parse_int(value) == expected


# ---------------------------------------------------------------------------
# vehicle_matches
# ---------------------------------------------------------------------------

def make_settings(**overrides):
    base = dict(
        telegram_bot_token="token",
        telegram_chat_id="chat",
        min_year=2024,
        max_price=28000,
        trim_keywords=("rear-wheel drive", "rwd"),
    )
    base.update(overrides)
    return tracker.Settings(**base)


def make_vehicle(**overrides):
    base = dict(
        vin="VIN123",
        price=27000,
        year=2024,
        model="Model Y",
        trim="Long Range RWD",
        mileage=1000,
        location="London",
        listing_url="https://example.com",
    )
    base.update(overrides)
    return tracker.Vehicle(**base)


def test_vehicle_matches_rejects_older_year():
    settings = make_settings()
    vehicle = make_vehicle(year=2023)
    assert tracker.vehicle_matches(vehicle, {}, settings) is False


def test_vehicle_matches_rejects_higher_price():
    settings = make_settings()
    vehicle = make_vehicle(price=29000)
    assert tracker.vehicle_matches(vehicle, {}, settings) is False


def test_vehicle_matches_checks_trim_keywords():
    settings = make_settings()
    vehicle = make_vehicle()
    record = {"TrimName": "Long Range All-Wheel Drive"}
    assert tracker.vehicle_matches(vehicle, record, settings) is False

    record = {"TrimName": "Long Range RWD"}
    assert tracker.vehicle_matches(vehicle, record, settings) is True


def test_vehicle_matches_empty_keywords_matches_everything():
    settings = make_settings(trim_keywords=())
    vehicle = make_vehicle()
    assert tracker.vehicle_matches(vehicle, {"TrimName": "Performance AWD"}, settings) is True


# ---------------------------------------------------------------------------
# find_alerts
# ---------------------------------------------------------------------------

def test_find_alerts_new_vehicle():
    vehicle = make_vehicle(vin="NEWVIN")
    alerts = tracker.find_alerts([vehicle], previous_state={})
    assert len(alerts) == 1
    assert alerts[0].kind == "new"
    assert alerts[0].vehicle.vin == "NEWVIN"


def test_find_alerts_price_drop():
    vehicle = make_vehicle(vin="VIN1", price=26000)
    previous_state = {
        "VIN1": {"price": 27000, "active": True},
    }
    alerts = tracker.find_alerts([vehicle], previous_state)
    assert len(alerts) == 1
    assert alerts[0].kind == "price_drop"
    assert alerts[0].previous_price == 27000


def test_find_alerts_no_change_no_alert():
    vehicle = make_vehicle(vin="VIN1", price=27000)
    previous_state = {
        "VIN1": {"price": 27000, "active": True},
    }
    alerts = tracker.find_alerts([vehicle], previous_state)
    assert alerts == []


def test_find_alerts_price_increase_no_alert():
    vehicle = make_vehicle(vin="VIN1", price=28000)
    previous_state = {
        "VIN1": {"price": 27000, "active": True},
    }
    alerts = tracker.find_alerts([vehicle], previous_state)
    assert alerts == []


def test_find_alerts_relisted_vehicle_fires():
    """Regression test: a delisted-then-relisted VIN must alert again,
    even though its VIN already exists in previous_state."""
    vehicle = make_vehicle(vin="VIN1", price=27000)
    previous_state = {
        "VIN1": {"price": 27000, "active": False},
    }
    alerts = tracker.find_alerts([vehicle], previous_state)
    assert len(alerts) == 1
    assert alerts[0].kind == "relisted"


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------

def test_save_state_marks_missing_vehicles_inactive(tmp_path):
    path = tmp_path / "state.json"
    previous_state = {
        "OLDVIN": {
            "vin": "OLDVIN",
            "price": 25000,
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
            "active": True,
        }
    }
    vehicle = make_vehicle(vin="NEWVIN")
    tracker.save_state([vehicle], previous_state, path=path)

    saved = json.loads(path.read_text())
    assert saved["OLDVIN"]["active"] is False
    assert saved["NEWVIN"]["active"] is True


def test_save_state_preserves_first_seen(tmp_path):
    path = tmp_path / "state.json"
    previous_state = {
        "VIN1": {
            "vin": "VIN1",
            "price": 27000,
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
            "active": True,
        }
    }
    vehicle = make_vehicle(vin="VIN1", price=26000)
    tracker.save_state([vehicle], previous_state, path=path)

    saved = json.loads(path.read_text())
    assert saved["VIN1"]["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert saved["VIN1"]["price"] == 26000


# ---------------------------------------------------------------------------
# send_alerts resilience (regression test for the partial-failure bug)
# ---------------------------------------------------------------------------

class _FailOnSecond:
    """Stand-in for send_telegram_alert that fails on the 2nd call only."""

    def __init__(self):
        self.calls = 0

    def __call__(self, alert, settings, session=None):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("simulated Telegram failure")


def test_send_alerts_continues_after_one_failure(monkeypatch):
    fake_sender = _FailOnSecond()
    monkeypatch.setattr(tracker, "send_telegram_alert", fake_sender)

    vehicles = [make_vehicle(vin=f"VIN{i}") for i in range(3)]
    alerts = [tracker.Alert(kind="new", vehicle=v) for v in vehicles]

    failures = tracker.send_alerts(alerts, make_settings(), session=object())

    assert fake_sender.calls == 3  # all three were attempted
    assert failures == 1


def test_main_saves_state_even_if_an_alert_fails(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(tracker, "STATE_FILE", state_path)

    settings = make_settings()
    monkeypatch.setattr(tracker.Settings, "from_environment", classmethod(lambda cls: settings))
    monkeypatch.setattr(tracker, "build_session", lambda: object())
    monkeypatch.setattr(tracker, "load_state", lambda path=state_path: {})

    vehicle = make_vehicle(vin="VIN1")
    monkeypatch.setattr(tracker, "fetch_inventory", lambda settings, session=None: [{}])
    monkeypatch.setattr(tracker, "filter_inventory", lambda records, settings: [vehicle])

    def failing_send(alert, settings, session=None):
        raise RuntimeError("simulated Telegram failure")

    monkeypatch.setattr(tracker, "send_telegram_alert", failing_send)

    saved_calls = []

    def fake_save_state(vehicles, previous_state, path=state_path):
        saved_calls.append(vehicles)

    monkeypatch.setattr(tracker, "save_state", fake_save_state)

    exit_code = tracker.main()

    assert exit_code == 1  # failure is still surfaced
    assert len(saved_calls) == 1  # but state was saved regardless
    assert saved_calls[0] == [vehicle]