from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from comfyui2api.__main__ import main, parse_args


class CliTests(unittest.TestCase):
    def test_parse_args_defaults_to_ui_command_later(self) -> None:
        args = parse_args([])
        self.assertIsNone(args.command)

    def test_ui_mode_sets_localhost_and_opens_browser(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("comfyui2api.__main__.open_browser_later") as mock_open, patch(
            "comfyui2api.__main__.uvicorn.run"
        ) as mock_run:
            main(["ui", "--port", "9010"])

            mock_open.assert_called_once_with("http://127.0.0.1:9010/ui")
            self.assertEqual(os.environ["API_LISTEN"], "127.0.0.1")
            self.assertEqual(os.environ["API_PORT"], "9010")
            self.assertEqual(mock_run.call_args.kwargs["host"], "127.0.0.1")

    def test_serve_disable_ui_sets_environment(self) -> None:
        with patch.dict(os.environ, {"API_LISTEN": "1.2.3.4", "API_PORT": "8001"}, clear=True), patch(
            "comfyui2api.__main__.open_browser_later"
        ) as mock_open, patch("comfyui2api.__main__.uvicorn.run") as mock_run:
            main(["serve", "--disable-ui"])

            mock_open.assert_not_called()
            self.assertEqual(os.environ["API_LISTEN"], "1.2.3.4")
            self.assertEqual(os.environ["API_PORT"], "8001")
            self.assertEqual(os.environ["COMFYUI2API_DISABLE_UI"], "1")
            self.assertEqual(mock_run.call_args.kwargs["host"], "1.2.3.4")


if __name__ == "__main__":
    unittest.main()
