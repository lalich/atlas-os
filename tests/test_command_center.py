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
        self.assertIn("Atlas OS overview", response.body)
        self.assertIn("Local development mode", response.body)

    def test_greenrock_page_route_returns_200_and_renders_approvals(self) -> None:
        with _isolated_env():
            main(["greenrock", "report-draft"])
            response = dispatch_request("GET", "/greenrock")

        self.assertEqual(response.status, 200)
        self.assertIn("GreenRock local review", response.body)
        self.assertIn("Approvals", response.body)
        self.assertIn("pending", response.body)
        self.assertIn("Approve", response.body)
        self.assertIn("Reject", response.body)

    def test_task_board_route_returns_200_and_can_create_task(self) -> None:
        with _isolated_env():
            create_response = dispatch_request(
                "POST",
                "/tasks",
                "name=Review+monthly+packet&division=greenrock",
            )
            response = dispatch_request("GET", "/tasks")

        self.assertEqual(create_response.status, 303)
        self.assertEqual(response.status, 200)
        self.assertIn("Review monthly packet", response.body)
        self.assertIn("Manual task board", response.body)

    def test_agent_monitor_route_returns_200(self) -> None:
        with _isolated_env():
            response = dispatch_request("GET", "/agents")

        self.assertEqual(response.status, 200)
        self.assertIn("Atlas Core", response.body)
        self.assertIn("GreenRock Analyst Agent", response.body)
        self.assertIn("inactive placeholder", response.body)


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
