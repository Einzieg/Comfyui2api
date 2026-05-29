import type React from "react";
import { CalendarClock, RotateCcw, Search } from "lucide-react";
import type { TaskFilters } from "../lib/api";

interface TaskFiltersProps {
  filters: TaskFilters;
  onChange: (filters: TaskFilters) => void;
  onApply: () => void;
  onReset: () => void;
}

export function TaskFiltersBar({ filters, onChange, onApply, onReset }: TaskFiltersProps): React.ReactElement {
  const update = (key: keyof TaskFilters, value: string): void => {
    onChange({ ...filters, [key]: value || undefined });
  };

  return (
    <section className="filters-bar" aria-label="任务筛选">
      <label className="date-field">
        <CalendarClock size={16} />
        <input
          type="datetime-local"
          value={filters.start ?? ""}
          onChange={(event) => update("start", event.target.value)}
          title="开始时间"
        />
      </label>
      <label className="date-field">
        <span>~</span>
        <input
          type="datetime-local"
          value={filters.end ?? ""}
          onChange={(event) => update("end", event.target.value)}
          title="结束时间"
        />
      </label>
      <label className="search-field">
        <Search size={16} />
        <input
          value={filters.q ?? ""}
          onChange={(event) => update("q", event.target.value)}
          placeholder="任务 ID / prompt_id"
        />
      </label>
      <select value={filters.status ?? ""} onChange={(event) => update("status", event.target.value)} title="状态">
        <option value="">状态</option>
        <option value="pending,queued">等待/排队</option>
        <option value="running">运行中</option>
        <option value="completed">成功</option>
        <option value="failed">失败</option>
      </select>
      <select value={filters.kind ?? ""} onChange={(event) => update("kind", event.target.value)} title="类型">
        <option value="">类型</option>
        <option value="txt2img">文生图</option>
        <option value="img2img">图生图</option>
        <option value="txt2video">文生视频</option>
        <option value="img2video">图生视频</option>
      </select>
      <select value={filters.platform ?? ""} onChange={(event) => update("platform", event.target.value)} title="平台">
        <option value="">平台</option>
        <option value="OpenAI">OpenAI</option>
        <option value="New-API">New-API</option>
        <option value="Native">Native</option>
        <option value="Chat">Chat</option>
      </select>
      <button className="ghost-button" type="button" onClick={onReset}>
        <RotateCcw size={15} />
        重置
      </button>
      <button className="primary-button" type="button" onClick={onApply}>
        查询
      </button>
    </section>
  );
}
