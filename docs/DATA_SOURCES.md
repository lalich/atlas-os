# Atlas OS Data Sources

Atlas OS defaults to mock data. Real market data is optional, local, and still approval-gated.

## GreenRock Data Modes

Mock mode:

```bash
atlas greenrock report-draft --data mock
```

Mock mode uses local generated sample price and volume history. It does not call external APIs and remains the default.

Real mode:

```bash
atlas greenrock report-draft --data real
atlas greenrock report-draft --data real --selection ranked
atlas greenrock report-draft --data real --selection strict
```

Real mode is production-shaped but fail-closed. If no provider is configured, Atlas prints a blocked message and does not create a report, approval, artifact, email, publication, or external action.

The browser Command Center uses the same modes. On `http://127.0.0.1:8000/greenrock`, **Run Mock Report** sends `data_mode=mock` and **Run Real Report** sends `data_mode=real`.

## Selection Modes

Mock mode defaults to `strict`, requiring all GreenRock criteria. Real mode defaults to `ranked`, which scores all valid names in each universe and selects the best available candidates when strict criteria produce too few names. `strict` remains available for real mode when the operator wants to see only full criteria passes.

## Optional Provider Settings

For the first real provider, Atlas supports a yfinance adapter.

Local `.env` placeholders:

```text
ATLAS_MARKET_DATA_PROVIDER=yfinance
ATLAS_GREENROCK_REAL_TICKERS=
```

When `ATLAS_GREENROCK_REAL_TICKERS` is blank, Atlas uses the local GreenRock universe CSVs.

## GreenRock Universes

GreenRock universes are operator-managed local ticker lists used as the default real-mode universe set for the Picks Board and report.

```bash
atlas greenrock universe list
atlas greenrock universe add TSLA PLTR
atlas greenrock universe remove TSLA
atlas greenrock universe reset-mega-rock
atlas greenrock universe reset-large-cap
atlas greenrock universe reset-small-mid
atlas greenrock universe reset-all
```

Universes are stored locally at:

```text
.atlas/output/greenrock/universes/mega_rock.csv
.atlas/output/greenrock/universes/large_cap.csv
.atlas/output/greenrock/universes/small_mid_cap.csv
```

The default lists contain real U.S.-listed tickers intended as starting universes only. Universe inclusion does not imply suitability, recommendation, or expected performance.

Optional dependency:

```bash
python3 -m pip install -e ".[market-data]"
```

## Picks Board Data Source

The GreenRock Picks Board at `/greenrock/picks` reads the latest run-specific CSV artifacts and inherits that run's data mode and report data source. A mock run displays a MOCK data badge. A real yfinance run displays REAL data and the report source, such as `yfinance:greenrock_universes`.

The board targets:

- Mega Rock: 1/1
- Large Cap: 11/11
- Small/Mid: 11/11

Real-data market-cap buckets:

- Mega Rock: market cap at or above $1T.
- Large Cap: $10B to below $1T.
- Small/Mid: below $10B.

If any section is incomplete, the board and report show a data quality warning.

Finviz links are plain outbound reference links in the format:

```text
https://finviz.com/quote.ashx?t=<TICKER>
```

The Picks Board does not publish, email, distribute, or bypass approval.

Do not commit real credentials or private data. The yfinance scaffold does not require an API key, but any future paid provider settings must stay local.

## Safety Rules

- No client files.
- No IBKR trading.
- No Gmail.
- No email sending.
- No auto-publishing.
- No recommendations sent anywhere.
- Human approval remains mandatory for every report draft.

Real-data reports are still drafts. They must not be used client-facing until a human approves the linked Atlas OS approval record and the appropriate review process is complete.
