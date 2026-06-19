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
                self.assertIn("# GreenRock Analysts Monthly Report", latest_report_contents)
                self.assertIn(f"**Run ID:** {second_run_id}", latest_report_contents)

                latest_run = _run_cli(["greenrock", "latest-run"])
                self.assertIn(second_run_id, latest_run)
                self.assertIn("approval_status: pending", latest_run)
                self.assertIn("artifact_count: 4", latest_run)

                latest_candidates = _run_cli(["greenrock", "latest-candidates"])
                self.assertIn(second_run_id, latest_candidates)
                self.assertIn("Large-cap candidates", latest_candidates)
                self.assertIn("Small-cap candidates", latest_candidates)
                self.assertIn("LC01", latest_candidates)
                self.assertIn("SC01", latest_candidates)

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
                self.assertIn("artifact_count: 8", dashboard)
                self.assertIn(second_run_id, dashboard)


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


if __name__ == "__main__":
    unittest.main()

