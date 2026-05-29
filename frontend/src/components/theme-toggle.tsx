import type React from "react";
import { Moon, Sun } from "lucide-react";

export type ThemeMode = "light" | "dark";

export function ThemeToggle({ theme, onToggle }: { theme: ThemeMode; onToggle: () => void }): React.ReactElement {
  const Icon = theme === "dark" ? Sun : Moon;
  return (
    <button className="icon-text-button" type="button" onClick={onToggle} title="切换主题">
      <Icon size={16} />
      <span>浅色/深色</span>
    </button>
  );
}
