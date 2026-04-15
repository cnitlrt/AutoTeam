import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTime(ts?: number | null) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { hour12: false });
}

export function formatRelative(ts?: number | null) {
  if (!ts) return "-";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return `${Math.floor(diff)}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  return `${Math.floor(diff / 86400)}天前`;
}

export function formatResetIn(ts?: number | null) {
  if (!ts) return "-";
  const diff = ts - Date.now() / 1000;
  if (diff <= 0) return "已重置";
  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)}天 ${h % 24}时`;
  if (h > 0) return `${h}时 ${m}分`;
  return `${m}分`;
}

export function formatDuration(start?: number | null, end?: number | null) {
  if (!start) return "-";
  const e = end ?? Date.now() / 1000;
  const s = Math.max(0, e - start);
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
}
