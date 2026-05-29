from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from comfyui2api.job_store import JobStore
from comfyui2api.jobs import Job, JobOutput, progress_percent_from_progress


class JobStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_list_filter_and_get_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "tasks.db")
            await store.init()
            job = Job(
                job_id="task_1",
                created_at=1780000000,
                created_at_utc="2026-05-29T00:19:20Z",
                started_at_utc="2026-05-29T00:19:25Z",
                finished_at_utc="2026-05-29T00:20:20Z",
                duration_s=60,
                status="completed",
                kind="txt2img",
                workflow="wf.json",
                platform="OpenAI",
                prompt_id="prompt_1",
                progress_percent=100,
                request_json={"prompt": "cat"},
                outputs=[
                    JobOutput(
                        filename="out.png",
                        url="/runs/task_1/out.png",
                        media_type="image/png",
                        node_id="1",
                        output_key="images",
                    )
                ],
            )

            await store.upsert_job(job)
            await store.replace_outputs(job.job_id, job.outputs)

            listed = await store.list_tasks(statuses=["completed"], kinds=["txt2img"], platforms=["OpenAI"], q="prompt_1")
            self.assertEqual(listed["total"], 1)
            self.assertEqual(listed["counts"]["completed"], 1)
            self.assertEqual(listed["items"][0]["job_id"], "task_1")
            self.assertEqual(listed["items"][0]["request_json"]["prompt"], "cat")

            detail = await store.get_task("task_1")
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["outputs"][0]["media_type"], "image/png")

    async def test_mark_unfinished_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "tasks.db")
            await store.init()
            await store.upsert_job(
                Job(
                    job_id="running",
                    created_at=1,
                    created_at_utc="2026-05-29T00:00:00Z",
                    status="running",
                    kind="txt2img",
                    workflow="wf.json",
                )
            )

            await store.mark_unfinished_interrupted()
            detail = await store.get_task("running")
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail["task"]["status"], "failed")
            self.assertEqual(detail["task"]["error"], "Task interrupted by server restart.")
            self.assertEqual(detail["task"]["progress_percent"], 100)

    async def test_progress_percent_calculation(self) -> None:
        self.assertEqual(progress_percent_from_progress({"value": 3, "max": 10}, status="running"), 30)
        self.assertEqual(progress_percent_from_progress({"value": 20, "max": 10}, status="running"), 99)
        self.assertEqual(progress_percent_from_progress({}, status="completed"), 100)


if __name__ == "__main__":
    unittest.main()
