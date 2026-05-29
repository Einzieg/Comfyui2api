import { useCallback, useEffect, useMemo, useState } from "react";
import type React from "react";
import {
  AuthError,
  getStats,
  getTask,
  listTasks,
  type AdminStats,
  type TaskDetailResponse,
  type TaskFilters,
  type TaskListResponse,
  type TaskRecord,
  type TaskStatus
} from "./lib/api";
import { clearAdminToken, getAdminToken, setAdminToken } from "./lib/auth";
import { connectAdminSocket } from "./lib/websocket";
import { AppShell } from "./components/app-shell";
import { SettingsPanel } from "./components/settings-panel";
import { StatusCards } from "./components/status-cards";
import { TaskFiltersBar } from "./components/task-filters";
import { TaskPreviewDrawer } from "./components/task-preview-drawer";
import { TaskTable } from "./components/task-table";
import { TokenGate } from "./components/token-gate";
import type { ThemeMode } from "./components/theme-toggle";

const zeroCounts: Record<TaskStatus, number> = {
  pending: 0,
  queued: 0,
  running: 0,
  completed: 0,
  failed: 0
};

const emptyList: TaskListResponse = {
  total: 0,
  counts: zeroCounts,
  items: []
};

export function App(): React.ReactElement {
  const [theme, setTheme] = useState<ThemeMode>(() => (window.localStorage.getItem("comfyui2api.theme") as ThemeMode) || "light");
  const [filters, setFilters] = useState<TaskFilters>({});
  const [appliedFilters, setAppliedFilters] = useState<TaskFilters>({});
  const [tasks, setTasks] = useState<TaskListResponse>(emptyList);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [authNeeded, setAuthNeeded] = useState(false);
  const [live, setLive] = useState(false);
  const [selectedTask, setSelectedTask] = useState<TaskRecord | null>(null);
  const [detail, setDetail] = useState<TaskDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("comfyui2api.theme", theme);
  }, [theme]);

  const apiFilters = useMemo(() => toApiFilters(appliedFilters), [appliedFilters]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [taskList, nextStats] = await Promise.all([listTasks(apiFilters), getStats()]);
      setTasks(taskList);
      setStats(nextStats);
      setAuthNeeded(false);
    } catch (err) {
      if (err instanceof AuthError) {
        clearAdminToken();
        setAuthNeeded(true);
      } else {
        setError(err instanceof Error ? err.message : "加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, [apiFilters]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (authNeeded) return;
    let closed = false;
    let retryId = 0;
    let socket = connectAdminSocket({
      token: getAdminToken(),
      onOpen: () => {
        setLive(true);
      },
      onClose: () => {
        setLive(false);
        if (!closed) {
          retryId = window.setTimeout(() => {
            socket = connectAdminSocket({
              token: getAdminToken(),
              onOpen: () => setLive(true),
              onClose: () => setLive(false),
              onEvent: handleWsEvent
            });
          }, 2000);
        }
      },
      onEvent: handleWsEvent
    });

    function handleWsEvent(event: { type: string; data?: TaskListResponse; job?: TaskRecord }): void {
      if (event.type === "snapshot" && event.data) {
        setTasks(event.data);
      }
      if (event.type === "task_updated" && event.job) {
        setTasks((current) => mergeTask(current, event.job as TaskRecord));
      }
    }

    return () => {
      closed = true;
      window.clearTimeout(retryId);
      socket.close();
    };
  }, [authNeeded]);

  useEffect(() => {
    if (authNeeded || live) return;
    const id = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(id);
  }, [authNeeded, live, refresh]);

  useEffect(() => {
    if (!selectedTask) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    getTask(selectedTask.job_id)
      .then((payload) => {
        setDetail(payload);
        setAuthNeeded(false);
      })
      .catch((err: unknown) => {
        if (err instanceof AuthError) {
          clearAdminToken();
          setAuthNeeded(true);
        } else {
          setError(err instanceof Error ? err.message : "详情加载失败");
        }
      })
      .finally(() => setDetailLoading(false));
  }, [selectedTask]);

  if (authNeeded) {
    return (
      <TokenGate
        onSubmit={(token) => {
          setAdminToken(token);
          setAuthNeeded(false);
          void refresh();
        }}
      />
    );
  }

  const counts = stats?.counts ?? tasks.counts ?? zeroCounts;
  const total = tasks.total || Object.values(counts).reduce((sum, value) => sum + value, 0);

  return (
    <AppShell
      stats={stats}
      theme={theme}
      live={live}
      loading={loading}
      onThemeToggle={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
      onRefresh={() => void refresh()}
      onSettings={() => setSettingsOpen(true)}
    >
      {error ? <div className="error-banner">{error}</div> : null}
      <StatusCards counts={counts} total={total} workerConcurrency={stats?.worker_concurrency} />
      <TaskFiltersBar
        filters={filters}
        onChange={setFilters}
        onApply={() => setAppliedFilters(filters)}
        onReset={() => {
          setFilters({});
          setAppliedFilters({});
        }}
      />
      <TaskTable items={tasks.items} total={tasks.total} onOpenTask={setSelectedTask} />
      <TaskPreviewDrawer
        task={selectedTask}
        detail={detail}
        loading={detailLoading}
        onClose={() => setSelectedTask(null)}
      />
      {settingsOpen ? <SettingsPanel stats={stats} onClose={() => setSettingsOpen(false)} /> : null}
    </AppShell>
  );
}

function toApiFilters(filters: TaskFilters): TaskFilters {
  return {
    ...filters,
    start: filters.start ? new Date(filters.start).toISOString() : undefined,
    end: filters.end ? new Date(filters.end).toISOString() : undefined
  };
}

function mergeTask(current: TaskListResponse, task: TaskRecord): TaskListResponse {
  const existing = current.items.filter((item) => item.job_id !== task.job_id);
  return {
    ...current,
    items: [task, ...existing].slice(0, 200)
  };
}
