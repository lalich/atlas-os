"""Tests for the local Atlas Command Center."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_os.cli import build_parser, main
from atlas_os.web_app import dispatch_request


class CommandCenterTests(unittest.TestCase):
    def test_atlas_serve_command_exists(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(args.command, "serve")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)

    def test_dashboard_route_returns_200(self) -> None:
        with _isolated_env():
            response = dispatch_request("GET", "/")

        self.assertEqual(response.status, 200)
        self.assertIn("Atlas Inbox", response.body)
        self.assertIn("What needs your attention", response.body)
        self.assertIn("Pending Approvals", response.body)
        self.assertIn("Development Mode", response.body)
        self.assertIn("Last Refresh:", response.body)

    def test_project_directory_route_returns_200(self) -> None:
        with _isolated_env():
            response = dispatch_request("GET", "/projects")

        self.assertEqual(response.status, 200)
        self.assertIn("Project Directory", response.body)
        self.assertIn("GreenRock Analysts", response.body)
        self.assertIn("Variance Capital / The Bat Signal", response.body)

    def test_greenrock_page_route_returns_200_and_renders_approvals(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/greenrock")

        self.assertEqual(response.status, 200)
        self.assertIn("Report Review Console", response.body)
        self.assertIn("Approvals", response.body)
        self.assertIn("pending", response.body)
        self.assertIn("Approve", response.body)
        self.assertIn("Reject", response.body)
        self.assertIn("GreenRock Score", response.body)
        self.assertIn("Signal Label", response.body)
        self.assertIn("Mega Rock Ticker Universe", response.body)
        self.assertIn("AAPL", response.body)

    def test_task_board_route_returns_200_and_can_create_task(self) -> None:
        with _isolated_env():
            create_response = dispatch_request(
                "POST",
                "/tasks",
                "name=Review+monthly+packet&division=greenrock&notes=Confirm+mock+data",
            )
            update_response = dispatch_request("POST", "/tasks/1/status", "status=awaiting_review")
            response = dispatch_request("GET", "/tasks")

        self.assertEqual(create_response.status, 303)
        self.assertEqual(update_response.status, 303)
        self.assertEqual(response.status, 200)
        self.assertIn("Review monthly packet", response.body)
        self.assertIn("Manual Operator Queue", response.body)
        self.assertIn("Confirm mock data", response.body)
        self.assertIn("Awaiting Review", response.body)

    def test_agent_monitor_route_returns_200(self) -> None:
        with _isolated_env():
            response = dispatch_request("GET", "/agents")

        self.assertEqual(response.status, 200)
        self.assertIn("Planned Agent HUD", response.body)
        self.assertIn("Atlas Core", response.body)
        self.assertIn("GreenRock Analyst Agent", response.body)
        self.assertIn("inactive", response.body)
        self.assertIn("planned", response.body)

    def test_approval_confirmation_route_works(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/approvals/1/confirm?action=approve")

        self.assertEqual(response.status, 200)
        self.assertIn("Human Approval Gate", response.body)
        self.assertIn("Approve Approval 1", response.body)
        self.assertIn("Approve locally", response.body)

    def test_browser_approve_action_updates_approval_state(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            decide_response = dispatch_request(
                "POST",
                "/approvals/1/decide",
                "action=approve&return_to=/greenrock",
            )
            greenrock = dispatch_request("GET", "/greenrock")

        self.assertEqual(decide_response.status, 303)
        self.assertIn("approved", greenrock.body)
        self.assertIn("Export PDF after approval", greenrock.body)

    def test_browser_reject_action_updates_approval_state(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            decide_response = dispatch_request(
                "POST",
                "/approvals/1/decide",
                "action=reject&return_to=/greenrock",
            )
            greenrock = dispatch_request("GET", "/greenrock")

        self.assertEqual(decide_response.status, 303)
        self.assertIn("rejected", greenrock.body)

    def test_dashboard_renders_pending_approval_indicators(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/")

        self.assertEqual(response.status, 200)
        self.assertIn("1</strong><h2>Pending Approvals", response.body)
        self.assertIn("Review GreenRock Report", response.body)
        self.assertIn("Latest Data Source", response.body)

    def test_greenrock_browser_pdf_export_works_for_approved_report(self) -> None:
        with _isolated_env() as root:
            main(["greenrock", "report-draft"])
            dispatch_request("POST", "/approvals/1/decide", "action=approve&return_to=/greenrock")
            export_response = dispatch_request("POST", "/greenrock/approvals/1/export-pdf")
            reports = dispatch_request("GET", "/reports")
            pdfs = list(root.glob("output/greenrock/*/greenrock_report_final.pdf"))
            pdf_is_valid = len(pdfs) == 1 and pdfs[0].read_bytes().startswith(b"%PDF")

        self.assertEqual(export_response.status, 303)
        self.assertEqual(len(pdfs), 1)
        self.assertTrue(pdf_is_valid)
        self.assertIn("greenrock_report_final.pdf", reports.body)

    def test_pending_approval_pdf_export_returns_safe_blocked_response(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("POST", "/greenrock/approvals/1/export-pdf")

        self.assertEqual(response.status, 400)
        self.assertIn("PDF Export Blocked", response.body)
        self.assertIn("requires an approved report", response.body)

    def test_invalid_approval_id_returns_clean_error(self) -> None:
        with _isolated_env():
            response = dispatch_request("POST", "/greenrock/approvals/not-a-number/export-pdf")

        self.assertEqual(response.status, 400)
        self.assertIn("Invalid Approval", response.body)
        self.assertIn("Approval ID must be a number", response.body)

    def test_malformed_greenrock_export_route_does_not_crash(self) -> None:
        with _isolated_env():
            response = dispatch_request("POST", "/greenrock/approvals/export-pdf")

        self.assertEqual(response.status, 404)
        self.assertIn("Route Not Found", response.body)


class _isolated_env:
    def __enter__(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.patch = patch.dict(
            "os.environ",
            {
                "ATLAS_DB_PATH": str(root / "atlas.db"),
                "ATLAS_OUTPUT_DIR": str(root / "output"),
            },
            clear=False,
        )
        self.patch.__enter__()
        return root

    def __exit__(self, exc_type, exc, tb):
        self.patch.__exit__(exc_type, exc, tb)
        self.temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
