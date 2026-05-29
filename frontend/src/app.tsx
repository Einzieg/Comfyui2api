import { useCallback, useEffect, useMemo, useState } from "react";
import type React from "react";
import {
  AuthError,
  getStats,
  getTask,
  listWorkflows,
  listTasks,
  shutdownApp,
  type AdminStats,
  type TaskDetailResponse,
  type TaskFilters,
  type TaskListResponse,
  type TaskRecord,
  type TaskStatus,
  type WorkflowListResponse
} from "./lib/api";
import { clearAdminToken, getAdminToken, setAdminToken } from "./lib/auth";
import { connectAdminSocket } from "./lib/websocket";
import { AppShell } from "./components/app-shell";
import type { DashboardView } from "./components/app-shell";
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
  const [activeView, setActiveView] = useState<DashboardView>("tasks");
  const [workflows, setWorkflows] = useState<WorkflowListResponse | null>(null);
  const [workflowsLoading, setWorkflowsLoading] = useState(false);
  const [quitting, setQuitting] = useState(false);

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

  const refreshWorkflows = useCallback(async () => {
    setWorkflowsLoading(true);
    setError("");
    try {
      setWorkflows(await listWorkflows());
      setAuthNeeded(false);
    } catch (err) {
      if (err instanceof AuthError) {
        clearAdminToken();
        setAuthNeeded(true);
      } else {
        setError(err instanceof Error ? err.message : "工作流加载失败");
      }
    } finally {
      setWorkflowsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authNeeded || activeView !== "workflows") return;
    void refreshWorkflows();
  }, [activeView, authNeeded, refreshWorkflows]);

  const handleShutdown = useCallback(async () => {
    if (quitting) return;
    if (!window.confirm("确定要退出 comfyui2api 吗？当前服务会停止。")) return;
    setQuitting(true);
    setError("");
    try {
      await shutdownApp();
      setError("comfyui2api 正在退出，窗口可以关闭。");
    } catch (err) {
      setQuitting(false);
      if (err instanceof AuthError) {
        clearAdminToken();
        setAuthNeeded(true);
      } else {
        setError(err instanceof Error ? err.message : "退出失败");
      }
    }
  }, [quitting]);

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
  const viewMeta = viewTitles[activeView];

  return (
    <AppShell
      stats={stats}
      theme={theme}
      live={live}
      loading={loading}
      quitting={quitting}
      activeView={activeView}
      title={viewMeta.title}
      subtitle={viewMeta.subtitle}
      onNavigate={setActiveView}
      onThemeToggle={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
      onRefresh={() => void refresh()}
      onSettings={() => setSettingsOpen(true)}
      onShutdown={() => void handleShutdown()}
    >
      {error ? <div className="error-banner">{error}</div> : null}
      {activeView === "overview" ? (
        <>
          <StatusCards counts={counts} total={total} workerConcurrency={stats?.worker_concurrency} />
          <OverviewPanel stats={stats} total={total} />
        </>
      ) : null}
      {activeView === "tasks" ? (
        <>
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
        </>
      ) : null}
      {activeView === "workflows" ? (
        <WorkflowPanel workflows={workflows} loading={workflowsLoading} onRefresh={() => void refreshWorkflows()} />
      ) : null}
      {activeView === "outputs" ? <OutputsPanel tasks={tasks.items} onOpenTask={setSelectedTask} /> : null}
      {activeView === "comfyui" ? <RuntimePanel stats={stats} kind="comfyui" /> : null}
      {activeView === "workers" ? <RuntimePanel stats={stats} kind="workers" /> : null}
      <TaskPreviewDrawer
        task={selectedTask}
        detail={detail}
        loading={detailLoading}
        onClose={() => setSelectedTask(null)}
      />
      {settingsOpen ? (
        <SettingsPanel
          stats={stats}
          shuttingDown={quitting}
          onClose={() => setSettingsOpen(false)}
          onShutdown={() => void handleShutdown()}
        />
      ) : null}
    </AppShell>
  );
}

const viewTitles: Record<DashboardView, { title: string; subtitle: string }> = {
  overview: { title: "概览", subtitle: "运行状态、任务总览和本地目录" },
  tasks: { title: "任务记录", subtitle: "队列、状态、耗时、输出预览与失败原因" },
  workflows: { title: "工作流", subtitle: "已加载工作流、类型和加载状态" },
  outputs: { title: "输出文件", subtitle: "最近任务产物和预览入口" },
  comfyui: { title: "ComfyUI", subtitle: "上游服务地址和连接状态" },
  workers: { title: "Workers", subtitle: "后台 worker 并发和任务状态" }
};

function OverviewPanel({ stats, total }: { stats: AdminStats | null; total: number }): React.ReactElement {
  return (
    <section className="view-panel">
      <div className="panel-header">
        <div>
          <h2>本地运行概况</h2>
          <p>当前任务总数 {total}</p>
        </div>
      </div>
      <div className="runtime-grid">
        <RuntimeTile label="ComfyUI" value={stats?.comfyui_base_url} />
        <RuntimeTile label="工作流目录" value={stats?.workflows_dir} />
        <RuntimeTile label="输出目录" value={stats?.runs_dir} />
        <RuntimeTile label="任务数据库" value={stats?.database_path} />
      </div>
    </section>
  );
}

function WorkflowPanel({
  workflows,
  loading,
  onRefresh
}: {
  workflows: WorkflowListResponse | null;
  loading: boolean;
  onRefresh: () => void;
}): React.ReactElement {
  const items = workflows?.items ?? [];
  return (
    <section className="view-panel">
      <div className="panel-header">
        <div>
          <h2>工作流列表</h2>
          <p>{workflows?.workflows_dir ?? "工作流目录"}</p>
        </div>
        <button className="ghost-button" type="button" onClick={onRefresh} disabled={loading}>
          刷新
        </button>
      </div>
      <div className="workflow-list">
        {items.map((item) => (
          <div className={item.available ? "workflow-row" : "workflow-row is-error"} key={item.name}>
            <div>
              <strong>{item.name}</strong>
              <span>{item.kind ?? "unknown"}</span>
            </div>
            <small>{item.available ? "可用" : item.load_error ?? item.parameter_error ?? "加载失败"}</small>
          </div>
        ))}
        {!loading && items.length === 0 ? <div className="empty-state compact">没有已加载的工作流</div> : null}
        {loading ? <div className="empty-state compact">正在加载工作流</div> : null}
      </div>
    </section>
  );
}

function OutputsPanel({
  tasks,
  onOpenTask
}: {
  tasks: TaskRecord[];
  onOpenTask: (task: TaskRecord) => void;
}): React.ReactElement {
  const outputTasks = tasks.filter((task) => task.output_count > 0 || task.url);
  return (
    <section className="view-panel">
      <div className="panel-header">
        <div>
          <h2>最近输出</h2>
          <p>显示当前已载入任务中的产物</p>
        </div>
      </div>
      <div className="output-grid">
        {outputTasks.map((task) => (
          <button className="output-card" type="button" onClick={() => onOpenTask(task)} key={task.job_id}>
            <strong>{task.workflow}</strong>
            <span>{task.job_id}</span>
            <small>{task.output_count} 个文件</small>
          </button>
        ))}
        {outputTasks.length === 0 ? <div className="empty-state compact">没有可显示的输出文件</div> : null}
      </div>
    </section>
  );
}

function RuntimePanel({ stats, kind }: { stats: AdminStats | null; kind: "comfyui" | "workers" }): React.ReactElement {
  const rows: Array<[string, string | undefined]> =
    kind === "comfyui"
      ? [
          ["服务地址", stats?.comfyui_base_url],
          ["工作流目录", stats?.workflows_dir],
          ["Web UI", stats?.ui_enabled ? "启用" : "禁用"]
        ]
      : [
          ["并发数", String(stats?.worker_concurrency ?? "--")],
          ["运行中", String(stats?.counts.running ?? 0)],
          ["排队中", String((stats?.counts.pending ?? 0) + (stats?.counts.queued ?? 0))]
        ];
  return (
    <section className="view-panel">
      <div className="runtime-grid">
        {rows.map(([label, value]) => (
          <RuntimeTile label={label} value={value} key={label} />
        ))}
      </div>
    </section>
  );
}

function RuntimeTile({ label, value }: { label: string; value?: string }): React.ReactElement {
  return (
    <div className="runtime-card">
      <span>{label}</span>
      <strong>{value ?? "--"}</strong>
    </div>
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
