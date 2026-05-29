export function formatDateTime(value?: string | null): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return "--";
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  const remain = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remain}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function compactId(value: string, size = 18): string {
  if (value.length <= size) return value;
  const head = Math.max(6, Math.floor(size * 0.55));
  const tail = Math.max(4, size - head - 1);
  return `${value.slice(0, head)}...${value.slice(-tail)}`;
}

export function asJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}
