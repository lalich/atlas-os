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
```

Real mode is production-shaped but fail-closed. If no provider is configured, Atlas prints a blocked message and does not create a report, approval, artifact, email, publication, or external action.

## Optional Provider Settings

For the first real provider, Atlas supports a yfinance adapter.

Local `.env` placeholders:

```text
ATLAS_MARKET_DATA_PROVIDER=yfinance
ATLAS_GREENROCK_REAL_TICKERS=
```

When `ATLAS_GREENROCK_REAL_TICKERS` is blank, Atlas uses the local Mega Rock universe.

## Mega Rock Universe

The Mega Rock universe is an operator-managed local ticker list used as the default real-mode universe for GreenRock.

```bash
atlas greenrock universe list
atlas greenrock universe add TSLA PLTR
atlas greenrock universe remove TSLA
atlas greenrock universe reset-mega-rock
```

The universe is stored locally at:

```text
.atlas/output/greenrock/universes/mega_rock.csv
```

The default Mega Rock list contains large, liquid U.S.-listed names intended as a starting universe only. Universe inclusion does not imply suitability, recommendation, or expected performance.

Optional dependency:

```bash
python3 -m pip install -e ".[market-data]"
```

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
