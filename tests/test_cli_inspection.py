"""Tests for CLI inspection commands."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main


class CliInspectionTests(unittest.TestCase):
    def test_run_artifact_and_audit_inspection_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "atlas.db"
            output_dir = root / "output"
            env = {
                "ATLAS_DB_PATH": str(db_path),
                "ATLAS_OUTPUT_DIR": str(output_dir),
            }

            with patch.dict("os.environ", env, clear=False):
                create_output = _run_cli(["greenrock", "report-draft"])
                run_id = _line_value(create_output, "run_id")
                approval_id = int(_line_value(create_output, "approval_id"))

                runs_list = _run_cli(["runs", "list"])
                self.assertIn(run_id, runs_list)

                runs_show = _run_cli(["runs", "show", run_id])
                self.assertIn("screen_candidates", runs_show)
                self.assertIn("blocked_for_approval", runs_show)

                artifacts_list = _run_cli(["artifacts", "list"])
                self.assertIn("report_draft_md", artifacts_list)

                artifact_id = _first_artifact_id(artifacts_list)
                artifacts_show = _run_cli(["artifacts", "show", artifact_id])
                self.assertIn("artifact_type:", artifacts_show)

                audit_list = _run_cli(["audit", "list"])
                self.assertIn("workflow_run_created", audit_list)

                audit_id = _first_audit_id(audit_list)
                audit_show = _run_cli(["audit", "show", audit_id])
                self.assertIn("action:", audit_show)

                approve_output = _run_cli(["approvals", "approve", str(approval_id)])
                self.assertIn("approved", approve_output)

                updated_run = _run_cli(["runs", "show", run_id])
                self.assertIn("status: approved", updated_run)


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


def _first_artifact_id(output: str) -> str:
    for line in output.splitlines():
        if " report_draft_md " in line:
            return line.split()[0]
    raise AssertionError(f"Missing report artifact in output:\n{output}")


def _first_audit_id(output: str) -> str:
    for line in output.splitlines():
        if "workflow_run_created" in line:
            return line.split()[0]
    raise AssertionError(f"Missing workflow audit in output:\n{output}")


if __name__ == "__main__":
    unittest.main()

