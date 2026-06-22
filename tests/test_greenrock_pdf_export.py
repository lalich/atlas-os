"""Tests for approved GreenRock PDF export."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import main


class GreenRockPdfExportTests(unittest.TestCase):
    def test_pending_approval_cannot_export_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                approval_id = _line_value(draft_output, "approval_id")

                export_output, exit_code = _run_cli_raw(["greenrock", "export-pdf", approval_id])

            self.assertEqual(exit_code, 1)
            self.assertIn("PDF export blocked", export_output)
            self.assertEqual(list(root.glob("output/greenrock/*/greenrock_report_final.pdf")), [])

    def test_approved_report_exports_pdf_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                run_id = _line_value(draft_output, "run_id")
                approval_id = _line_value(draft_output, "approval_id")
                _run_cli(["approvals", "approve", approval_id])

                export_output = _run_cli(["greenrock", "export-pdf", approval_id])
                artifacts = _run_cli(["artifacts", "list"])

            expected_pdf = root / "output" / "greenrock" / run_id / "greenrock_report_final.pdf"
            self.assertTrue(expected_pdf.exists())
            self.assertTrue(expected_pdf.read_bytes().startswith(b"%PDF"))
            self.assertIn(str(expected_pdf), export_output)
            self.assertIn("report_final_pdf", artifacts)
            self.assertIn(str(expected_pdf), artifacts)

    def test_pdf_path_is_run_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                first = _run_cli(["greenrock", "report-draft"])
                second = _run_cli(["greenrock", "report-draft"])
                first_run = _line_value(first, "run_id")
                second_run = _line_value(second, "run_id")
                first_approval = _line_value(first, "approval_id")
                second_approval = _line_value(second, "approval_id")
                _run_cli(["approvals", "approve", first_approval])
                _run_cli(["approvals", "approve", second_approval])
                _run_cli(["greenrock", "export-pdf", first_approval])
                _run_cli(["greenrock", "export-pdf", second_approval])

            first_pdf = root / "output" / "greenrock" / first_run / "greenrock_report_final.pdf"
            second_pdf = root / "output" / "greenrock" / second_run / "greenrock_report_final.pdf"
            self.assertTrue(first_pdf.exists())
            self.assertTrue(second_pdf.exists())
            self.assertNotEqual(first_pdf, second_pdf)

    def test_repeated_export_does_not_create_duplicate_pdf_artifact_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                approval_id = _line_value(draft_output, "approval_id")
                _run_cli(["approvals", "approve", approval_id])
                first_export = _run_cli(["greenrock", "export-pdf", approval_id])
                second_export = _run_cli(["greenrock", "export-pdf", approval_id])
                artifacts = _run_cli(["artifacts", "list"])

            self.assertEqual(artifacts.count("report_final_pdf"), 1)
            self.assertEqual(_line_value(first_export, "artifact_id"), _line_value(second_export, "artifact_id"))

    def test_final_packet_shows_required_fields_for_approved_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                approval_id = _line_value(draft_output, "approval_id")
                _run_cli(["approvals", "approve", approval_id])
                _run_cli(["greenrock", "export-pdf", approval_id])
                packet = _run_cli(["greenrock", "final-packet", approval_id])

            self.assertIn("GreenRock Final Report Packet", packet)
            self.assertIn("approval_status: approved", packet)
            self.assertIn("run_id:", packet)
            self.assertIn("markdown_report_path:", packet)
            self.assertIn("pdf_path:", packet)
            self.assertIn("report_final_pdf", packet)
            self.assertIn("mock_data_disclaimer:", packet)
            self.assertIn("human_approval_confirmation: approved", packet)

    def test_pending_approval_cannot_produce_final_packet_as_final(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                approval_id = _line_value(draft_output, "approval_id")
                packet, exit_code = _run_cli_raw(["greenrock", "final-packet", approval_id])

            self.assertEqual(exit_code, 1)
            self.assertIn("approval_status: pending", packet)
            self.assertIn("not approved - this is not a final packet", packet)

    def test_open_pdf_handles_missing_pdf_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                approval_id = _line_value(draft_output, "approval_id")
                _run_cli(["approvals", "approve", approval_id])
                output, exit_code = _run_cli_raw(["greenrock", "open-pdf", approval_id])

            self.assertEqual(exit_code, 1)
            self.assertIn("No exported PDF found", output)
            self.assertIn("atlas greenrock export-pdf", output)

    def test_open_pdf_opens_exported_pdf_on_macos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            }
            with patch.dict("os.environ", env, clear=False):
                draft_output = _run_cli(["greenrock", "report-draft"])
                approval_id = _line_value(draft_output, "approval_id")
                _run_cli(["approvals", "approve", approval_id])
                _run_cli(["greenrock", "export-pdf", approval_id])
                with (
                    patch("atlas_os.cli.sys.platform", "darwin"),
                    patch("atlas_os.cli.subprocess.run") as mocked_open,
                ):
                    output = _run_cli(["greenrock", "open-pdf", approval_id])

            self.assertIn("Opened GreenRock PDF:", output)
            mocked_open.assert_called_once()


def _run_cli(args: list[str]) -> str:
    output, exit_code = _run_cli_raw(args)
    if exit_code != 0:
        raise AssertionError(f"CLI exited with {exit_code}: {args}\n{output}")
    return output


def _run_cli_raw(args: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = main(args)
    return buffer.getvalue(), exit_code


def _line_value(output: str, label: str) -> str:
    prefix = f"{label}: "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise AssertionError(f"Missing {label} in output:\n{output}")


if __name__ == "__main__":
    unittest.main()
