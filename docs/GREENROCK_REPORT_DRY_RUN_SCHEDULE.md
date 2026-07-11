# GreenRock Report Dry-Run Schedule

Phase 11B adds local schedule evaluation for GreenRock Report Agent dry runs. It does not install a daemon, cron job, macOS launch agent, email sender, publisher, broker integration, approval record, PDF export, client contact path, or external LLM/API call.

## Commands

```bash
atlas greenrock report-schedule preview
atlas greenrock report-schedule preview --count 5
atlas greenrock report-schedule run-due
```

`preview` lists upcoming local schedule occurrences without creating a report. `run-due` checks whether any scheduled dry run is due at the current local time and creates only missing local markdown drafts.

The direct command remains available:

```bash
atlas greenrock report-dry-run
```

## Local Config

The schedule config is created on demand at:

```text
.atlas/output/greenrock/report_dry_runs/schedule_config.json
```

Default fields:

- `timezone`: `America/New_York`
- `month_end_hour`: `19`
- `month_end_minute`: `0`
- `sunday_refresh_enabled`: `true`
- `sunday_refresh_hour`: `11`
- `sunday_refresh_minute`: `0`
- `market_holidays`: optional `YYYY-MM-DD` dates that should not count as trading days

## Schedule Logic

Atlas finds the last trading day of the month by walking backward from the calendar month end until it finds a weekday that is not listed in `market_holidays`.

The main dry run is scheduled for the previous trading day at the configured evening time, defaulting to 19:00 America/New_York. If the last trading day is Monday, that previous trading day is Friday evening at the same default time.

When the last trading day is Monday and `sunday_refresh_enabled` is true, Atlas also schedules a Sunday morning refresh before that Monday, defaulting to 11:00 America/New_York.

Every generated markdown dry run includes:

- `scheduled_for`
- `generated_at`
- `schedule_reason`
- `review_required`

## Duplicate Prevention

Scheduled runs are recorded in:

```text
.atlas/output/greenrock/report_dry_runs/schedule_runs.json
```

Each occurrence has a deterministic `occurrence_id`, such as `greenrock-report-2026-08-month-end` or `greenrock-report-2026-08-sunday-refresh`. `run-due` skips any occurrence already present in the ledger.

## Safety Boundary

Scheduled dry runs are local review drafts only. Human review is required every time. No report approval, PDF export, email, publishing, client contact, brokerage action, order construction, or external LLM/API action is created by the schedule path.
