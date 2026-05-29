import type React from "react";
import { Activity, Files, LayoutDashboard, Power, RefreshCw, Settings, Workflow, Zap } from "lucide-react";
import type { AdminStats, TaskStatus } from "../lib/api";
import { ThemeToggle, type ThemeMode } from "./theme-toggle";

export type DashboardView = "overview" | "tasks" | "workflows" | "outputs" | "comfyui" | "workers";

interface AppShellProps {
  children: React.ReactNode;
  stats: AdminStats | null;
  theme: ThemeMode;
  live: boolean;
  loading: boolean;
  quitting: boolean;
  activeView: DashboardView;
  title: string;
  subtitle: string;
  onNavigate: (view: DashboardView) => void;
  onThemeToggle: () => void;
  onRefresh: () => void;
  onSettings: () => void;
  onShutdown: () => void;
}

const emptyCounts: Record<TaskStatus, number> = {
  pending: 0,
  queued: 0,
  running: 0,
  completed: 0,
  failed: 0
};

export function AppShell({
  children,
  stats,
  theme,
  live,
  loading,
  quitting,
  activeView,
  title,
  subtitle,
  onNavigate,
  onThemeToggle,
  onRefresh,
  onSettings,
  onShutdown
}: AppShellProps): React.ReactElement {
  const counts = stats?.counts ?? emptyCounts;
  const running = counts.running ?? 0;
  const navItems: Array<{ view: DashboardView; icon: React.ReactNode; label: string }> = [
    { view: "overview", icon: <LayoutDashboard size={16} />, label: "概览" },
    { view: "tasks", icon: <Activity size={16} />, label: "任务记录" },
    { view: "workflows", icon: <Workflow size={16} />, label: "工作流" },
    { view: "outputs", icon: <Files size={16} />, label: "输出文件" }
  ];
  const runtimeItems: Array<{ view: DashboardView; icon: React.ReactNode; label: string }> = [
    { view: "comfyui", icon: <Zap size={16} />, label: "ComfyUI" },
    { view: "workers", icon: <RefreshCw size={16} />, label: "Workers" }
  ];

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div>
            <strong>comfyui2api</strong>
            <span>Local API Dashboard</span>
          </div>
        </div>
        <nav className="side-nav" aria-label="主导航">
          <span>控制台</span>
          {navItems.map((item) => (
            <button
              className={activeView === item.view ? "active" : ""}
              type="button"
              onClick={() => onNavigate(item.view)}
              key={item.view}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
          <button type="button" onClick={onSettings}>
            <Settings size={16} />
            设置
          </button>
          <span>运行时</span>
          {runtimeItems.map((item) => (
            <button
              className={activeView === item.view ? "active" : ""}
              type="button"
              onClick={() => onNavigate(item.view)}
              key={item.view}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>
        <div className="connection-card">
          <div className={live ? "pulse-dot online" : "pulse-dot"} />
          <strong>{live ? "实时同步" : "轮询同步"}</strong>
          <span>{stats?.comfyui_base_url ?? "ComfyUI"}</span>
        </div>
      </aside>
      <main className="main-shell">
        <header className="topbar">
          <div>
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </div>
          <div className="top-actions">
            <span className={live ? "sync-pill online" : "sync-pill"}>
              <span />
              {live ? "实时同步" : "自动轮询"}
            </span>
            <span className="kbd-pill">⌘ K</span>
            <ThemeToggle theme={theme} onToggle={onThemeToggle} />
            <button className="icon-text-button" type="button" onClick={onSettings} title="设置">
              <Settings size={16} />
              设置
            </button>
            <button className="danger-button" type="button" onClick={onShutdown} disabled={quitting} title="退出应用">
              <Power size={16} />
              {quitting ? "正在退出" : "退出"}
            </button>
            <button className="primary-button" type="button" onClick={onRefresh} disabled={loading}>
              <RefreshCw size={16} />
              刷新队列
            </button>
          </div>
        </header>
        <div className="running-line">当前运行中 {running} 个任务</div>
        {children}
      </main>
    </div>
  );
}
