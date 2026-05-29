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

from comfyui2api.config import load_config, package_resource_dir, runtime_base_dir


class RuntimePathTests(unittest.TestCase):
    def test_runtime_base_dir_source_mode(self) -> None:
        self.assertEqual(runtime_base_dir(), PROJECT_ROOT)
        self.assertEqual(package_resource_dir(), PROJECT_ROOT / "src" / "comfyui2api")

    def test_load_config_adds_data_ui_and_admin_fields(self) -> None:
        with patch.dict(
            os.environ,
            {
                "API_TOKEN": "api-token",
                "ADMIN_TOKEN": "",
                "DATA_DIR": str(PROJECT_ROOT / "tmp-data"),
                "DATABASE_PATH": str(PROJECT_ROOT / "tmp-data" / "tasks.db"),
                "COMFYUI2API_DISABLE_UI": "1",
            },
            clear=False,
        ):
            cfg = load_config()

        self.assertEqual(cfg.admin_token, "api-token")
        self.assertEqual(cfg.data_dir, PROJECT_ROOT / "tmp-data")
        self.assertEqual(cfg.database_path, PROJECT_ROOT / "tmp-data" / "tasks.db")
        self.assertFalse(cfg.ui_enabled)


if __name__ == "__main__":
    unittest.main()
