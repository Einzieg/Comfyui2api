import type React from "react";
import { CheckCircle2, X } from "lucide-react";
import type { AdminStats } from "../lib/api";

export function SettingsPanel({ stats, onClose }: { stats: AdminStats | null; onClose: () => void }): React.ReactElement {
  return (
    <div className="drawer-layer" role="dialog" aria-modal="true">
      <button className="drawer-scrim" type="button" onClick={onClose} aria-label="关闭" />
      <aside className="settings-drawer">
        <div className="drawer-title">
          <div>
            <h2>启动与运行设置</h2>
            <span>UI 模式、命令行模式、端口、鉴权和持久目录</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>
        <section className="settings-section">
          <h3>运行时目录</h3>
          <RuntimeLine label="工作流目录" value={stats?.workflows_dir} />
          <RuntimeLine label="输出目录" value={stats?.runs_dir} />
          <RuntimeLine label="任务数据库" value={stats?.database_path} />
        </section>
        <section className="settings-section">
          <h3>服务状态</h3>
          <RuntimeLine label="ComfyUI" value={stats?.comfyui_base_url} />
          <RuntimeLine label="Workers" value={String(stats?.worker_concurrency ?? "--")} />
          <RuntimeLine label="Web UI" value={stats?.ui_enabled ? "启用" : "禁用"} />
        </section>
        <section className="settings-section badges-row">
          <span>127.0.0.1 安全默认</span>
          <span>Bearer Auth</span>
          <span>WebSocket token</span>
        </section>
      </aside>
    </div>
  );
}

function RuntimeLine({ label, value }: { label: string; value?: string }): React.ReactElement {
  return (
    <div className="runtime-line">
      <CheckCircle2 size={16} />
      <div>
        <strong>{label}</strong>
        <span>{value ?? "--"}</span>
      </div>
    </div>
  );
}
