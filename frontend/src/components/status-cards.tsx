import type React from "react";
import { Activity, AlertCircle, CheckCircle2, CircleDot, ListChecks } from "lucide-react";
import type { TaskStatus } from "../lib/api";

interface StatusCardsProps {
  counts: Record<TaskStatus, number>;
  total: number;
  workerConcurrency?: number;
}

export function StatusCards({ counts, total, workerConcurrency = 1 }: StatusCardsProps): React.ReactElement {
  const successRate = total > 0 ? Math.round((counts.completed / total) * 1000) / 10 : 0;
  const cards = [
    { label: "总任务", value: total, hint: "历史记录", icon: ListChecks, tone: "green" },
    { label: "运行中", value: counts.running, hint: `worker ${workerConcurrency}`, icon: Activity, tone: "blue" },
    { label: "排队中", value: counts.queued + counts.pending, hint: "等待 worker", icon: CircleDot, tone: "amber" },
    { label: "成功", value: counts.completed, hint: `成功率 ${successRate}%`, icon: CheckCircle2, tone: "green" },
    { label: "失败", value: counts.failed, hint: "最近记录", icon: AlertCircle, tone: "red" }
  ];

  return (
    <section className="status-cards" aria-label="任务统计">
      {cards.map((card) => (
        <div className="stat-card" key={card.label}>
          <div className={`stat-dot ${card.tone}`}>
            <card.icon size={14} />
          </div>
          <span>{card.label}</span>
          <strong>{card.value}</strong>
          <small>{card.hint}</small>
        </div>
      ))}
    </section>
  );
}
