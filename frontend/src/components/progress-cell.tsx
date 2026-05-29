import type React from "react";

export function ProgressCell({ value, status }: { value: number; status: string }): React.ReactElement {
  const pct = Math.max(0, Math.min(100, Math.round(value || 0)));
  return (
    <div className="progress-cell" aria-label={`progress ${pct}%`}>
      <div className="progress-track">
        <div className={`progress-fill ${status === "running" ? "is-running" : ""}`} style={{ width: `${pct}%` }} />
      </div>
      <span>{pct}%</span>
    </div>
  );
}
