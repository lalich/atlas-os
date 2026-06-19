"""Command line interface for Atlas OS."""

from __future__ import annotations

import argparse
from pathlib import Path

from atlas_os import __version__
from atlas_os.config import get_settings
from atlas_os.db.database import initialize_database
from atlas_os.greenrock.report import build_report_draft, build_sample_report
from atlas_os.greenrock.screener import run_screen, write_screen_outputs
from atlas_os.logging_config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Atlas OS local workflow runner.",
    )
    parser.add_argument("--version", action="version", version=f"atlas {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show local Atlas OS status.")

    greenrock = subparsers.add_parser("greenrock", help="GreenRock Analysts commands.")
    greenrock_subparsers = greenrock.add_subparsers(dest="greenrock_command")
    greenrock_subparsers.add_parser(
        "sample-report",
        help="Generate a local sample GreenRock report from mock data.",
    )
    greenrock_subparsers.add_parser(
        "run-screen",
        help="Run the local GreenRock mock screening engine and write CSV outputs.",
    )
    greenrock_subparsers.add_parser(
        "candidates",
        help="Print the current local GreenRock selected candidates.",
    )
    greenrock_subparsers.add_parser(
        "report-draft",
        help="Generate a local GreenRock draft report from mock screening data.",
    )

    return parser


def run_status() -> int:
    settings = get_settings()
    db_path = initialize_database(settings.db_path)
    print("Atlas OS status")
    print(f"version: {__version__}")
    print(f"environment: {settings.env}")
    print(f"database: {db_path}")
    print("external services: disabled")
    print("approval gate: required for client-facing publication")
    return 0


def run_greenrock_sample_report() -> int:
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    report = build_sample_report()
    output_path = Path(settings.output_dir) / "greenrock_sample_report.md"
    output_path.write_text(report.markdown, encoding="utf-8")
    print(f"Sample GreenRock report created: {output_path}")
    print("This is mock data only and is not approved for publication.")
    return 0


def run_greenrock_screen() -> int:
    settings = get_settings()
    result = run_screen()
    paths = write_screen_outputs(result, settings.output_dir)
    print("GreenRock local screen complete")
    print(f"selected candidates: {len(result.selected)}")
    print(f"all candidates CSV: {paths['all']}")
    print(f"large-cap CSV: {paths['large_cap']}")
    print(f"small-cap CSV: {paths['small_cap']}")
    print("Mock data only. No external services were used.")
    return 0


def run_greenrock_candidates() -> int:
    result = run_screen()
    print("GreenRock selected candidates")
    print("bucket symbol score company")
    for candidate in result.selected:
        print(
            f"{candidate.market_cap_bucket} "
            f"{candidate.symbol} "
            f"{candidate.score:.2f} "
            f"{candidate.company_name}"
        )
    return 0


def run_greenrock_report_draft() -> int:
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    write_screen_outputs(run_screen(), settings.output_dir)
    report = build_report_draft()
    output_path = Path(settings.output_dir) / "greenrock_report_draft.md"
    output_path.write_text(report.markdown, encoding="utf-8")
    print(f"GreenRock report draft created: {output_path}")
    print("Draft only. Human approval is required before client-facing use.")
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return run_status()

    if args.command == "greenrock":
        if args.greenrock_command == "sample-report":
            return run_greenrock_sample_report()
        if args.greenrock_command == "run-screen":
            return run_greenrock_screen()
        if args.greenrock_command == "candidates":
            return run_greenrock_candidates()
        if args.greenrock_command == "report-draft":
            return run_greenrock_report_draft()
        parser.error("greenrock requires a subcommand")

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
