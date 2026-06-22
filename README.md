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
atlas greenrock latest-report
atlas greenrock latest-report --print
atlas greenrock latest-run
atlas greenrock latest-candidates
atlas greenrock review
atlas greenrock open-latest
atlas greenrock export-pdf <approval_id>
atlas greenrock export-pdf <approval_id> --open
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
atlas greenrock open-pdf <approval_id>
atlas approvals list
atlas approvals pending
atlas approvals latest
atlas approvals show 1
atlas approvals approve 1
atlas approvals reject 1
atlas dashboard
atlas runs list
atlas runs show <run_id>
atlas artifacts list
atlas artifacts show <artifact_id>
atlas audit list
atlas audit show <audit_id>
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

- `.atlas/output/greenrock/<run_id>/greenrock_candidates.csv`
- `.atlas/output/greenrock/<run_id>/greenrock_large_cap.csv`
- `.atlas/output/greenrock/<run_id>/greenrock_small_cap.csv`
- `.atlas/output/greenrock/<run_id>/greenrock_report_draft.md`

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

## Run Inspection

Workflow runs persist step states, artifacts, report records, and audit log entries in local SQLite.

```bash
atlas runs list
atlas runs show <run_id>
atlas artifacts list
atlas artifacts show <artifact_id>
atlas audit list
atlas audit show <audit_id>
```

GreenRock report drafting uses step states of `initialized`, `running`, `completed`, `failed`, and `blocked_for_approval`. The draft report step remains blocked until the linked approval is approved.

## GreenRock Report Draft

`atlas greenrock report-draft` creates a professional mock GreenRock Analysts Monthly Report draft in the run-specific output folder:

```text
.atlas/output/greenrock/<run_id>/greenrock_report_draft.md
```

The draft includes an executive summary, methodology, large-cap and small-cap candidate tables, per-name screening rationale, risk notes, human approval language, mock-data language, and compliance-friendly disclaimers. It avoids guarantees and direct personalized recommendations.

## Analyst Shortcuts

These commands provide quick review views without needing to inspect raw database records:

```bash
atlas greenrock latest-report
atlas greenrock latest-report --print
atlas greenrock latest-run
atlas greenrock latest-candidates
atlas greenrock review
atlas greenrock open-latest
atlas greenrock export-pdf <approval_id>
atlas greenrock export-pdf <approval_id> --open
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
atlas greenrock open-pdf <approval_id>
atlas approvals pending
atlas approvals latest
atlas dashboard
```

`latest-report` finds the newest GreenRock report by workflow run timestamp. `latest-candidates` summarizes the latest run-specific large-cap and small/mid-cap CSV files. `review` shows the latest run, report path, approval status, top candidates, and pending approval ID. `open-latest` opens the latest report on macOS or prints the path on unsupported systems. `dashboard` shows recent runs, pending approvals, artifact counts, and the latest GreenRock report path.

## Approved PDF Export

PDF export is allowed only after the linked report approval is approved:

```bash
atlas approvals approve <approval_id>
atlas greenrock export-pdf <approval_id>
atlas greenrock export-pdf <approval_id> --open
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
atlas greenrock open-pdf <approval_id>
```

Approved exports are saved beside the Markdown report:

```text
.atlas/output/greenrock/<run_id>/greenrock_report_final.pdf
```

Repeated PDF exports update/reuse the same PDF path and do not create duplicate `report_final_pdf` artifact records for the same run.

## Final Approved Packet

Use the final packet view after approval to confirm the report, PDF, artifacts, and safety status:

```bash
atlas greenrock final-packet <approval_id>
atlas greenrock final-packet <approval_id> --print
```

Pending or rejected approvals are not treated as final packets. Use `atlas greenrock open-pdf <approval_id>` to open an exported approved PDF on macOS, or to see the next step if no PDF exists yet.

`atlas dashboard` also shows the latest GreenRock approval status, final PDF status, and final PDF path when exported.

## Operator Docs

- [Operator Runbook](docs/OPERATOR_RUNBOOK.md)
- [GreenRock Product Notes](docs/GREENROCK_PRODUCT_NOTES.md)
- [Monthly Report Release Checklist](docs/MONTHLY_REPORT_RELEASE_CHECKLIST.md)

## Tests

```bash
python3 -m unittest discover
```

## Safety Rule

Atlas OS must not publish, email, distribute, or otherwise release client-facing material without explicit human approval. Current workflows use local mock data only.

## Documentation

Planning documents live in [docs/README.md](docs/README.md).
