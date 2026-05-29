from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .jobs import Job, JobOutput
from .util import utc_now_iso, utc_now_unix


STATUSES = ("pending", "queued", "running", "completed", "failed")


class JobStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        await self._run_sync(self._init_sync)

    async def upsert_job(self, job: Job) -> None:
        await self._run_sync(self._upsert_job_sync, job)

    async def replace_outputs(self, job_id: str, outputs: list[JobOutput]) -> None:
        await self._run_sync(self._replace_outputs_sync, job_id, outputs)

    async def get_task(self, job_id: str) -> dict[str, Any] | None:
        return await self._run_sync(self._get_task_sync, job_id)

    async def list_tasks(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        q: str | None = None,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        platforms: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await self._run_sync(
            self._list_tasks_sync,
            start,
            end,
            q,
            statuses or None,
            kinds or None,
            platforms or None,
            max(1, min(200, int(limit))),
            max(0, int(offset)),
        )

    async def stats(self) -> dict[str, Any]:
        return await self._run_sync(self._stats_sync)

    async def mark_unfinished_interrupted(self) -> None:
        await self._run_sync(self._mark_unfinished_interrupted_sync)

    async def _run_sync(self, fn: Any, *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.database_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _init_sync(self) -> None:
        with closing(self._connect()) as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    job_id TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    started_at_utc TEXT,
                    finished_at_utc TEXT,
                    updated_at_utc TEXT,
                    duration_s INTEGER,

                    platform TEXT NOT NULL DEFAULT 'Native',
                    kind TEXT NOT NULL,
                    workflow TEXT NOT NULL,
                    requested_model TEXT,

                    status TEXT NOT NULL,
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    progress_json TEXT,

                    prompt_id TEXT,
                    queue_number INTEGER,
                    current_node TEXT,

                    url TEXT,
                    output_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,

                    prompt_preview TEXT,
                    request_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_kind ON tasks(kind);
                CREATE INDEX IF NOT EXISTS idx_tasks_platform ON tasks(platform);

                CREATE TABLE IF NOT EXISTS task_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    url TEXT NOT NULL,
                    media_type TEXT,
                    node_id TEXT,
                    output_key TEXT,
                    FOREIGN KEY(job_id) REFERENCES tasks(job_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_task_outputs_job_id ON task_outputs(job_id);
                """
            )
            con.commit()

    def _upsert_job_sync(self, job: Job) -> None:
        payload = _job_to_row(job)
        with closing(self._connect()) as con:
            con.execute(
                """
                INSERT INTO tasks (
                    job_id, created_at, created_at_utc, started_at_utc, finished_at_utc,
                    updated_at_utc, duration_s, platform, kind, workflow, requested_model,
                    status, progress_percent, progress_json, prompt_id, queue_number,
                    current_node, url, output_count, error, prompt_preview, request_json
                ) VALUES (
                    :job_id, :created_at, :created_at_utc, :started_at_utc, :finished_at_utc,
                    :updated_at_utc, :duration_s, :platform, :kind, :workflow, :requested_model,
                    :status, :progress_percent, :progress_json, :prompt_id, :queue_number,
                    :current_node, :url, :output_count, :error, :prompt_preview, :request_json
                )
                ON CONFLICT(job_id) DO UPDATE SET
                    started_at_utc=excluded.started_at_utc,
                    finished_at_utc=excluded.finished_at_utc,
                    updated_at_utc=excluded.updated_at_utc,
                    duration_s=excluded.duration_s,
                    platform=excluded.platform,
                    kind=excluded.kind,
                    workflow=excluded.workflow,
                    requested_model=excluded.requested_model,
                    status=excluded.status,
                    progress_percent=excluded.progress_percent,
                    progress_json=excluded.progress_json,
                    prompt_id=excluded.prompt_id,
                    queue_number=excluded.queue_number,
                    current_node=excluded.current_node,
                    url=excluded.url,
                    output_count=excluded.output_count,
                    error=excluded.error,
                    prompt_preview=excluded.prompt_preview,
                    request_json=excluded.request_json
                """,
                payload,
            )
            con.commit()

    def _replace_outputs_sync(self, job_id: str, outputs: list[JobOutput]) -> None:
        with closing(self._connect()) as con:
            con.execute("DELETE FROM task_outputs WHERE job_id = ?", (job_id,))
            con.executemany(
                """
                INSERT INTO task_outputs (job_id, filename, url, media_type, node_id, output_key)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        output.filename,
                        output.url,
                        output.media_type,
                        output.node_id,
                        output.output_key,
                    )
                    for output in outputs
                ],
            )
            con.execute("UPDATE tasks SET output_count = ? WHERE job_id = ?", (len(outputs), job_id))
            con.commit()

    def _get_task_sync(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            outputs = con.execute(
                """
                SELECT filename, url, media_type, node_id, output_key
                FROM task_outputs
                WHERE job_id = ?
                ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()
        return {"task": _task_from_row(row), "outputs": [_output_from_row(item) for item in outputs]}

    def _list_tasks_sync(
        self,
        start: str | None,
        end: str | None,
        q: str | None,
        statuses: list[str] | None,
        kinds: list[str] | None,
        platforms: list[str] | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        where, params = _build_filters(
            start=start,
            end=end,
            q=q,
            statuses=statuses,
            kinds=kinds,
            platforms=platforms,
        )
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        with closing(self._connect()) as con:
            total = int(con.execute(f"SELECT COUNT(*) FROM tasks{where_sql}", params).fetchone()[0])
            rows = con.execute(
                f"""
                SELECT * FROM tasks{where_sql}
                ORDER BY created_at DESC, job_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
            count_rows = con.execute(
                f"SELECT status, COUNT(*) AS count FROM tasks{where_sql} GROUP BY status",
                params,
            ).fetchall()

        counts = {status: 0 for status in STATUSES}
        for row in count_rows:
            counts[str(row["status"])] = int(row["count"])
        items = [_task_from_row(row) | {"outputs": []} for row in rows]
        return {"total": total, "counts": counts, "items": items}

    def _stats_sync(self) -> dict[str, Any]:
        with closing(self._connect()) as con:
            rows = con.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
        counts = {status: 0 for status in STATUSES}
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        return {"counts": counts}

    def _mark_unfinished_interrupted_sync(self) -> None:
        now_iso = utc_now_iso()
        now_unix = utc_now_unix()
        with closing(self._connect()) as con:
            con.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error = 'Task interrupted by server restart.',
                    finished_at_utc = ?,
                    updated_at_utc = ?,
                    duration_s = MAX(0, ? - created_at),
                    progress_percent = 100
                WHERE status IN ('pending', 'queued', 'running')
                """,
                (now_iso, now_iso, now_unix),
            )
            con.commit()


def _job_to_row(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "created_at": int(job.created_at),
        "created_at_utc": job.created_at_utc,
        "started_at_utc": job.started_at_utc or None,
        "finished_at_utc": job.finished_at_utc or None,
        "updated_at_utc": job.updated_at_utc or None,
        "duration_s": job.duration_s,
        "platform": job.platform or "Native",
        "kind": job.kind,
        "workflow": job.workflow,
        "requested_model": job.requested_model or None,
        "status": job.status,
        "progress_percent": int(job.progress_percent or 0),
        "progress_json": json.dumps(job.progress or {}, ensure_ascii=False),
        "prompt_id": job.prompt_id or None,
        "queue_number": job.queue_number,
        "current_node": job.current_node or None,
        "url": job.url or None,
        "output_count": len(job.outputs or []),
        "error": job.error or None,
        "prompt_preview": _truncate(job.prompt or ""),
        "request_json": json.dumps(job.request_json or {}, ensure_ascii=False),
    }


def _build_filters(
    *,
    start: str | None,
    end: str | None,
    q: str | None,
    statuses: list[str] | None,
    kinds: list[str] | None,
    platforms: list[str] | None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if start:
        where.append("created_at_utc >= ?")
        params.append(start)
    if end:
        where.append("created_at_utc <= ?")
        params.append(end)
    if q:
        like = f"%{q}%"
        where.append("(job_id LIKE ? OR prompt_id LIKE ? OR workflow LIKE ? OR error LIKE ?)")
        params.extend([like, like, like, like])
    _add_in_filter(where, params, "status", statuses)
    _add_in_filter(where, params, "kind", kinds)
    _add_in_filter(where, params, "platform", platforms)
    return where, params


def _add_in_filter(where: list[str], params: list[Any], column: str, values: list[str] | None) -> None:
    cleaned = [str(item).strip() for item in values or [] if str(item).strip()]
    if not cleaned:
        return
    where.append(f"{column} IN ({','.join('?' for _ in cleaned)})")
    params.extend(cleaned)


def _task_from_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["progress"] = _json_loads(item.pop("progress_json"), {})
    item["request_json"] = _json_loads(item.get("request_json"), {})
    return item


def _output_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _truncate(value: str, *, limit: int = 300) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"
