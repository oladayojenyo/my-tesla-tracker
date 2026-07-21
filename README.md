# UK Tesla Used Inventory Tracker

A Python and GitHub Actions tracker for used UK Tesla Model Y listings. It sends Telegram alerts when:

- a matching vehicle appears for the first time;
- a previously delisted vehicle reappears in inventory ("relisted"); or
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

## Reliability notes

- Sending a Telegram alert never blocks the others: each alert is sent independently, and a failure on one is logged and skipped rather than aborting the run.
- Inventory state is always written to disk after each run, even if one or more alerts failed to send. This prevents duplicate "new" alerts on the next run caused by a transient Telegram or network failure.
- The GitHub Actions workflow commits the updated state file even if the tracker step reports a failure (some alerts didn't send), so state and notifications never drift out of sync. The job itself still shows as failed so you notice and can investigate.

## Repository structure