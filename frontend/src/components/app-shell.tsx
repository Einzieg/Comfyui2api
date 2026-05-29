import type React from "react";
import { Activity, Files, LayoutDashboard, RefreshCw, Settings, Workflow, Zap } from "lucide-react";
import type { AdminStats, TaskStatus } from "../lib/api";
import { ThemeToggle, type ThemeMode } from "./theme-toggle";

interface AppShellProps {
  children: React.ReactNode;
  stats: AdminStats | null;
  theme: ThemeMode;
  live: boolean;
  loading: boolean;
  onThemeToggle: () => void;
  onRefresh: () => void;
  onSettings: () => void;
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
  onThemeToggle,
  onRefresh,
  onSettings
}: AppShellProps): React.ReactElement {
  const counts = stats?.counts ?? emptyCounts;
  const running = counts.running ?? 0;

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
          <button type="button">
            <LayoutDashboard size={16} />
            概览
          </button>
          <button className="active" type="button">
            <Activity size={16} />
            任务记录
          </button>
          <button type="button">
            <Workflow size={16} />
            工作流
          </button>
          <button type="button">
            <Files size={16} />
            输出文件
          </button>
          <button type="button" onClick={onSettings}>
            <Settings size={16} />
            设置
          </button>
          <span>运行时</span>
          <button type="button">
            <Zap size={16} />
            ComfyUI
          </button>
          <button type="button">
            <RefreshCw size={16} />
            Workers
          </button>
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
            <h1>任务记录</h1>
            <p>队列、状态、耗时、输出预览与失败原因</p>
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
