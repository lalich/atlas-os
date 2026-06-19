# Atlas OS

Atlas OS is a local-first Python scaffold for multi-division AI workflows with human approval gates.

The first implementation target is the GreenRock Analysts Monthly Report. Phase 0 uses mock/sample data only and does not connect to market data, email, client files, brokerage accounts, OneDrive, Gmail, or credential stores.

## Local Install

```bash
cd ~/Desktop/atlas-os
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## Offline Local Run

If the editable install cannot fetch Python build tools, use the included local launcher:

```bash
cd ~/Desktop/atlas-os
export PATH="$PWD/bin:$PATH"
atlas --help
atlas status
atlas greenrock sample-report
```

## CLI

```bash
atlas --help
atlas status
atlas greenrock sample-report
atlas greenrock run-screen
atlas greenrock candidates
atlas greenrock report-draft
atlas approvals list
atlas approvals show 1
atlas approvals approve 1
atlas approvals reject 1
```

You can also run the CLI without installing the console script:

```bash
python -m atlas_os.cli --help
python -m atlas_os.cli greenrock sample-report
python -m atlas_os.cli greenrock run-screen
```

## GreenRock Local Screening

Phase 1A includes a mock-data-only GreenRock screening engine. It calculates SMA, EMA, RSI, 2.5 standard deviation Bollinger Bands, 52-week low proximity, 10-day average volume trend, and moving average rate of change.

```bash
atlas greenrock run-screen
atlas greenrock candidates
atlas greenrock report-draft
```

The local screener writes:

- `.atlas/output/greenrock_candidates.csv`
- `.atlas/output/greenrock_large_cap.csv`
- `.atlas/output/greenrock_small_cap.csv`
- `.atlas/output/greenrock_report_draft.md`

The draft report is local mock output only and still requires human approval before any client-facing use.

## Approval Queue

GreenRock screening and report-draft commands persist a local workflow run to SQLite, store artifact records, and create a pending approval for the draft report.

```bash
atlas greenrock report-draft
atlas approvals list
atlas approvals show <id>
atlas approvals approve <id>
atlas approvals reject <id>
```

Report drafts remain blocked from client-facing use while their approval status is `pending` or `rejected`.

## Tests

```bash
python3 -m unittest discover
```

## Safety Rule

Atlas OS must not publish, email, distribute, or otherwise release client-facing material without explicit human approval. Phase 0 only produces local sample output.

## Documentation

Planning documents live in [docs/README.md](docs/README.md).
