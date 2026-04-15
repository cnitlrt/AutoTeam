import { cn } from "@/lib/utils";
import { Progress } from "@/components/ui/progress";

export function pctColor(pct: number | null | undefined) {
  if (pct == null) return "text-muted-foreground";
  if (pct >= 90) return "text-emerald-600 dark:text-emerald-400";
  if (pct >= 30) return "text-sky-600 dark:text-sky-400";
  return "text-rose-600 dark:text-rose-400";
}

export function indicatorColor(pct: number | null | undefined) {
  if (pct == null) return "bg-muted";
  if (pct >= 90) return "bg-emerald-500";
  if (pct >= 30) return "bg-sky-500";
  return "bg-rose-500";
}

export function QuotaBar({
  pct,
  label,
  className,
}: {
  pct: number | null | undefined;
  label?: string;
  className?: string;
}) {
  const shown = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className={cn("flex items-center gap-2 min-w-[120px]", className)}>
      <Progress value={shown} indicatorClassName={indicatorColor(pct)} className="flex-1" />
      <span className={cn("w-12 text-right font-mono text-xs", pctColor(pct))}>
        {pct == null ? "—" : `${pct.toFixed(0)}%`}
      </span>
      {label && <span className="text-[10px] text-muted-foreground">{label}</span>}
    </div>
  );
}
