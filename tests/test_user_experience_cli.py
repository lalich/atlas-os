"""Tests for analyst-friendly CLI shortcuts."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main


class UserExperienceCliTests(unittest.TestCase):
    def test_greenrock_latest_shortcuts_and_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }

            with patch.dict("os.environ", env, clear=False):
                first_output = _run_cli(["greenrock", "report-draft"])
                second_output = _run_cli(["greenrock", "report-draft"])
                first_run_id = _line_value(first_output, "run_id")
                second_run_id = _line_value(second_output, "run_id")

                latest_report = _run_cli(["greenrock", "latest-report"])
                self.assertIn(second_run_id, latest_report)
                self.assertIn(f"greenrock/{second_run_id}/greenrock_report_draft.md", latest_report)

                latest_report_contents = _run_cli(["greenrock", "latest-report", "--print"])
                self.assertIn("# GreenRock Analysts Monthly Opportunity Report", latest_report_contents)
                self.assertIn(f"**Run ID:** {second_run_id}", latest_report_contents)

                latest_run = _run_cli(["greenrock", "latest-run"])
                self.assertIn(second_run_id, latest_run)
                self.assertIn("approval_status: pending", latest_run)
                self.assertIn("artifact_count: 5", latest_run)

                latest_candidates = _run_cli(["greenrock", "latest-candidates"])
                self.assertIn(second_run_id, latest_candidates)
                self.assertIn("Large-cap candidates", latest_candidates)
                self.assertIn("Small-cap candidates", latest_candidates)
                self.assertIn("LC01", latest_candidates)
                self.assertIn("Small Cap Mock", latest_candidates)

                pending = _run_cli(["approvals", "pending"])
                self.assertIn(first_run_id, pending)
                self.assertIn(second_run_id, pending)

                latest_approval = _run_cli(["approvals", "latest"])
                self.assertIn(second_run_id, latest_approval)
                self.assertIn("status: pending", latest_approval)

                dashboard = _run_cli(["dashboard"])
                self.assertIn("Atlas OS Dashboard", dashboard)
                self.assertIn("Recent runs", dashboard)
                self.assertIn("Pending approvals", dashboard)
                self.assertIn("artifact_count: 10", dashboard)
                self.assertIn(second_run_id, dashboard)
                self.assertIn("Agent Cycle", dashboard)

                review = _run_cli(["greenrock", "review"])
                self.assertIn("GreenRock Review", review)
                self.assertIn(f"latest_run: {second_run_id}", review)
                self.assertIn("pending_approval_id:", review)
                self.assertIn("Top large-cap names", review)
                self.assertIn("Top small/mid-cap names", review)

                with (
                    patch("atlas_os.cli.sys.platform", "darwin"),
                    patch("atlas_os.cli.subprocess.run") as mocked_open,
                ):
                    mocked_open.return_value.returncode = 0
                    open_output = _run_cli(["greenrock", "open-latest"])
                self.assertIn("Opened latest GreenRock report:", open_output)
                mocked_open.assert_called_once()

    def test_agent_and_inbox_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }

            with patch.dict("os.environ", env, clear=False):
                agents = _run_cli(["agents", "list"])
                self.assertIn("Market Agent", agents)
                self.assertIn("Evidence Agent", agents)
                self.assertIn("Inbox Agent", agents)

                cycle = _run_cli(["agents", "run"])
                self.assertIn("safe_local_mode: true", cycle)
                self.assertIn("cycle_id:", cycle)
                cycle_id = _line_value(cycle, "cycle_id")
                self.assertIn("market: completed", cycle)
                self.assertIn("inbox: completed", cycle)
                self.assertIn("top_operator_actions:", cycle)
                self.assertIn("No email, publishing, trading", cycle)

                status = _run_cli(["agents", "status"])
                self.assertIn("last_agent_cycle:", status)
                self.assertIn("market completed", status)

                cycles = _run_cli(["agents", "cycles"])
                self.assertIn(cycle_id, cycles)

                cycle_detail = _run_cli(["agents", "cycle", cycle_id])
                self.assertIn("cycle_diff:", cycle_detail)
                self.assertIn("new_inbox_items", cycle_detail)

                inbox = _run_cli(["inbox", "list"])
                self.assertIn("Atlas Inbox", inbox)
                self.assertIn("Staging underfilled", inbox)
                item_id = _first_inbox_id(inbox)

                item = _run_cli(["inbox", "show", item_id])
                self.assertIn("related_agent_run_id:", item)
                self.assertIn("created_reason:", item)

                complete = _run_cli(["inbox", "complete", item_id])
                self.assertIn("inbox_item_completed:", complete)

                doctor = _run_cli(["doctor"])
                self.assertIn("Atlas Doctor", doctor)
                self.assertIn("ATLAS_MARKET_DATA_PROVIDER:", doctor)
                self.assertIn("yfinance_available:", doctor)
                self.assertIn("greenrock_logo_present:", doctor)
                self.assertIn("output_dir_writable:", doctor)
                self.assertIn("database_initialized:", doctor)

    def test_greenrock_score_audit_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }

            with patch.dict("os.environ", env, clear=False):
                single = _run_cli(["greenrock", "score-audit", "LC01", "--data", "mock"])
                self.assertIn("GreenRock Score Audit", single)
                self.assertIn("final_greenrock_score:", single)
                self.assertIn("component_scores:", single)
                self.assertIn("raw_technical_inputs:", single)
                self.assertIn("evidence_contributions:", single)
                self.assertIn("score_path_consistency:", single)
                self.assertIn("score_paths_agree: yes", single)

                multi = _run_cli(["greenrock", "score-audit", "LC01", "SC01", "--data", "mock"])
                self.assertEqual(multi.count("GreenRock Score Audit"), 2)


def _run_cli(args: list[str]) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    if exit_code != 0:
        raise AssertionError(f"CLI exited with {exit_code}: {args}")
    return buffer.getvalue()


def _line_value(output: str, label: str) -> str:
    prefix = f"{label}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise AssertionError(f"Missing {label} in output:\n{output}")


def _first_inbox_id(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("inbox-"):
            return line.split()[0]
    raise AssertionError(f"Missing inbox item in output:\n{output}")


if __name__ == "__main__":
    unittest.main()
