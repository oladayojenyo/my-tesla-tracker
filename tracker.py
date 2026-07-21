import os
import requests

TESLA_API_URL = "https://tesla.com"

# Configures the payload to query the official UK Used Inventory
PAYLOAD = {
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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def check_tesla_inventory():
    try:
        response = requests.post(TESLA_API_URL, json=PAYLOAD, timeout=10)
        results = response.json().get("results", [])
        matched_cars = []

        for car in results:
            price = int(car.get("Price", 999999))
            year = int(car.get("Year", 0))
            vin = car.get("VIN", "")

            # Adjust these variables if you want to tighten criteria
            if year == 2024 and price <= 26000:
                cpo_link = f"https://tesla.com{vin}?titleStatus=used"
                matched_cars.append({"price": price, "link": cpo_link, "vin": vin})

        return matched_cars
    except Exception as e:
        print(f"Error querying API: {e}")
        return []

def send_telegram_alert(car):
    message = (
        f"🚨 **TESLA PRICE DROP DETECTED!** 🚨\n\n"
        f"🚘 **Model Y RWD (2024)**\n"
        f"💰 Price: £{car['price']:,}\n"
        f"🆔 VIN: {car['vin']}\n\n"
        f"🔗 [View CPO Listing Directly]({car['link']})"
    )
    url = f"https://telegram.org{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

if __name__ == "__main__":
    for match in check_tesla_inventory():
        send_telegram_alert(match)
