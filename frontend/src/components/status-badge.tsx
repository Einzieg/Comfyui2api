import type React from "react";
import { CheckCircle2, Circle, LoaderCircle, XCircle } from "lucide-react";
import type { TaskStatus } from "../lib/api";

const labels: Record<TaskStatus, string> = {
  pending: "等待",
  queued: "排队中",
  running: "运行中",
  completed: "成功",
  failed: "失败"
};

export function StatusBadge({ status }: { status: TaskStatus }): React.ReactElement {
  const Icon = status === "completed" ? CheckCircle2 : status === "failed" ? XCircle : status === "running" ? LoaderCircle : Circle;
  return (
    <span className={`status-badge status-${status}`}>
      <Icon size={14} />
      {labels[status] ?? status}
    </span>
  );
}

export const kindLabels: Record<string, string> = {
  txt2img: "文生图",
  img2img: "图生图",
  txt2video: "文生视频",
  img2video: "图生视频"
};
