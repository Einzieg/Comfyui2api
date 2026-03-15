from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from comfyui2api.comfy_client import ComfyApiError, ComfyUIClient


class ComfyClientTests(unittest.IsolatedAsyncioTestCase):
    def test_local_loopback_client_bypasses_system_proxy(self) -> None:
        with patch("comfyui2api.comfy_client.httpx.AsyncClient") as mock_client:
            ComfyUIClient("http://127.0.0.1:8188")
        self.assertFalse(mock_client.call_args.kwargs["trust_env"])

    def test_remote_client_keeps_trust_env_enabled(self) -> None:
        with patch("comfyui2api.comfy_client.httpx.AsyncClient") as mock_client:
            ComfyUIClient("https://example.com")
        self.assertTrue(mock_client.call_args.kwargs["trust_env"])

    async def test_queue_prompt_http_error_includes_status_url_headers_and_body(self) -> None:
        client = ComfyUIClient("http://127.0.0.1:8188")
        response = httpx.Response(
            502,
            headers={"server": "nginx", "content-type": "text/plain"},
            text="",
            request=httpx.Request("POST", "http://127.0.0.1:8188/prompt"),
        )
        client._client.post = AsyncMock(return_value=response)

        with self.assertRaises(ComfyApiError) as ctx:
            await client.queue_prompt(prompt={"1": {}}, client_id="cid")

        message = str(ctx.exception)
        self.assertIn("status=502", message)
        self.assertIn("url=http://127.0.0.1:8188/prompt", message)
        self.assertIn("'server': 'nginx'", message)
        self.assertIn("body=''", message)
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
