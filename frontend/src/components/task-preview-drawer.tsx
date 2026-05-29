import type React from "react";
import { Copy, ExternalLink, FileQuestion, LoaderCircle, X } from "lucide-react";
import type { TaskDetailResponse, TaskOutput, TaskRecord } from "../lib/api";
import { asJson, compactId, formatDateTime, formatDuration } from "../lib/format";
import { kindLabels, StatusBadge } from "./status-badge";

interface TaskPreviewDrawerProps {
  task: TaskRecord | null;
  detail: TaskDetailResponse | null;
  loading: boolean;
  onClose: () => void;
}

export function TaskPreviewDrawer({ task, detail, loading, onClose }: TaskPreviewDrawerProps): React.ReactElement | null {
  if (!task) return null;
  const current = detail?.task ?? task;
  const outputs = detail?.outputs ?? task.outputs ?? [];
  const requestJson = current.request_json ?? {};

  return (
    <div className="drawer-layer" role="dialog" aria-modal="true">
      <button className="drawer-scrim" type="button" onClick={onClose} aria-label="关闭" />
      <aside className="task-drawer">
        <div className="drawer-title">
          <div>
            <h2>任务详情</h2>
            <span title={current.job_id}>{compactId(current.job_id, 34)}</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div className="drawer-loading">
            <LoaderCircle size={20} />
            加载中
          </div>
        ) : null}

        <div className="preview-stage">
          {outputs.length > 0 ? renderOutput(outputs[0]) : <EmptyPreview />}
        </div>

        <div className="detail-grid">
          <Info label="状态" value={<StatusBadge status={current.status} />} />
          <Info label="耗时" value={formatDuration(current.duration_s)} />
          <Info label="平台" value={current.platform} />
          <Info label="类型" value={kindLabels[current.kind] ?? current.kind} />
          <Info label="prompt_id" value={current.prompt_id ? compactId(current.prompt_id, 18) : "--"} />
          <Info label="queue_number" value={String(current.queue_number ?? "--")} />
          <Info label="提交时间" value={formatDateTime(current.created_at_utc)} />
          <Info label="结束时间" value={formatDateTime(current.finished_at_utc)} />
        </div>

        {current.error ? (
          <section className="drawer-section error-block">
            <div className="section-title">
              <h3>错误信息</h3>
              <button type="button" onClick={() => void navigator.clipboard.writeText(current.error ?? "")}>
                <Copy size={14} />
                复制
              </button>
            </div>
            <pre>{current.error}</pre>
          </section>
        ) : null}

        <section className="drawer-section">
          <h3>请求摘要</h3>
          <pre>{asJson(requestJson)}</pre>
        </section>

        <section className="drawer-section output-list">
          <h3>输出文件</h3>
          {outputs.length > 0 ? (
            outputs.map((output) => (
              <a href={output.url} target="_blank" rel="noreferrer" key={`${output.filename}-${output.url}`}>
                <span>{output.filename}</span>
                <ExternalLink size={14} />
              </a>
            ))
          ) : (
            <span className="muted">暂无输出</span>
          )}
        </section>

        <div className="drawer-actions">
          <button type="button" onClick={() => void navigator.clipboard.writeText(current.url ?? "")} disabled={!current.url}>
            <Copy size={15} />
            复制 URL
          </button>
          <a className={current.url ? "primary-button" : "primary-button disabled"} href={current.url ?? "#"} target="_blank" rel="noreferrer">
            <ExternalLink size={15} />
            打开输出文件
          </a>
        </div>
      </aside>
    </div>
  );
}

function Info({ label, value }: { label: string; value: React.ReactNode }): React.ReactElement {
  return (
    <div className="info-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function renderOutput(output: TaskOutput): React.ReactElement {
  const mediaType = output.media_type ?? "";
  if (mediaType.startsWith("image/")) {
    return <img src={output.url} alt={output.filename} />;
  }
  if (mediaType.startsWith("video/")) {
    return <video src={output.url} controls />;
  }
  return (
    <a className="file-preview" href={output.url} target="_blank" rel="noreferrer">
      <FileQuestion size={28} />
      <span>{output.filename}</span>
    </a>
  );
}

function EmptyPreview(): React.ReactElement {
  return (
    <div className="empty-preview">
      <FileQuestion size={30} />
      <span>暂无预览</span>
    </div>
  );
}
