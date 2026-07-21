# UK Tesla Used Inventory Tracker

A Python and GitHub Actions tracker for used UK Tesla Model Y listings. It sends Telegram alerts when:

- a matching vehicle appears for the first time; or
- the price of a previously seen matching vehicle drops.

The default search is:

- Model Y
- used inventory
- UK market
- model year 2024 or newer
- price at or below £28,000
- trim text containing `rear-wheel drive` or `rwd`

## Important limitation

Tesla's inventory endpoint is undocumented and can change without notice. The tracker deliberately fails the workflow if Tesla stops returning the expected JSON structure, rather than silently reporting that no cars were found.

## Repository structure

```text
.
├── .github/workflows/tesla_check.yml
├── tests/test_tracker.py
├── tracker.py
├── inventory_state.json
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Telegram setup

1. Open Telegram and message `@BotFather`.
2. Run `/newbot` and follow the prompts.
3. Copy the bot token.
4. Send a message to your new bot.
5. Open the following URL in a browser, replacing `<TOKEN>`:

   `https://api.telegram.org/bot<TOKEN>/getUpdates`

6. Find the `chat.id` value in the response.

## GitHub secrets

In the repository, open:

`Settings → Secrets and variables → Actions → New repository secret`

Create:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python tracker.py
```

For PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN="your-token"
$env:TELEGRAM_CHAT_ID="your-chat-id"
python tracker.py
```

## Configuration

Environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `TESLA_MIN_YEAR` | `2024` | Minimum model year |
| `TESLA_MAX_PRICE` | `28000` | Maximum price in GBP |
| `TESLA_TRIM_KEYWORDS` | `rear-wheel drive,rwd` | Comma-separated trim terms |
| `TESLA_PAGE_SIZE` | `50` | Results requested per page |
| `TESLA_MAX_PAGES` | `10` | Maximum pages per run |
| `HTTP_TIMEOUT_SECONDS` | `20` | HTTP timeout |
| `STATE_FILE` | `inventory_state.json` | Local state path |
| `LOG_LEVEL` | `INFO` | Python logging level |

To monitor every trim, set `TESLA_TRIM_KEYWORDS` to an empty value.

## State and duplicate prevention

`inventory_state.json` stores each VIN, latest price, first-seen time, last-seen time and whether it is still active. The workflow commits state changes using GitHub's Actions bot. This prevents repeated alerts for an unchanged listing.

The workflow requires `contents: write`. Some organisations restrict GitHub Actions from pushing. In that case, enable read/write workflow permissions under repository Actions settings or move the state to an external store.

## Test

```bash
pytest -q
ruff check tracker.py tests
```

## Manual run

Open the repository's **Actions** tab, choose **Tesla Inventory Tracker**, then select **Run workflow**.
