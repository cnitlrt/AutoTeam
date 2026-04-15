import { cn } from "@/lib/utils";
import type { AccountStatus } from "@/lib/types";

const MAP: Record<AccountStatus, { label: string; dot: string; bg: string; text: string }> = {
  active: {
    label: "激活",
    dot: "bg-emerald-500 dark:bg-emerald-400",
    bg: "bg-emerald-500/10 border-emerald-500/30",
    text: "text-emerald-700 dark:text-emerald-300",
  },
  standby: {
    label: "待用",
    dot: "bg-sky-500 dark:bg-sky-400",
    bg: "bg-sky-500/10 border-sky-500/30",
    text: "text-sky-700 dark:text-sky-300",
  },
  exhausted: {
    label: "耗尽",
    dot: "bg-amber-500 dark:bg-amber-400",
    bg: "bg-amber-500/10 border-amber-500/30",
    text: "text-amber-700 dark:text-amber-300",
  },
  pending: {
    label: "待处理",
    dot: "bg-zinc-500 dark:bg-zinc-400",
    bg: "bg-zinc-500/10 border-zinc-500/30",
    text: "text-zinc-700 dark:text-zinc-300",
  },
};

export function StatusBadge({ status }: { status: AccountStatus | string }) {
  const m = MAP[status as AccountStatus] ?? MAP.pending;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
        m.bg,
        m.text,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", m.dot)} />
      {m.label}
    </span>
  );
}
