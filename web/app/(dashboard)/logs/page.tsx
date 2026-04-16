"use client";
import { useEffect, useRef, useState } from "react";
import { Loader2, Pause, Play, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { VncViewer } from "@/components/feature/vnc-viewer";
import { cn } from "@/lib/utils";
import type { LogLine } from "@/lib/types";

function levelColor(level: string) {
  const l = level.toUpperCase();
  if (l.includes("ERROR") || l.includes("CRITICAL"))
    return "text-rose-600 dark:text-rose-300";
  if (l.includes("WARN")) return "text-amber-600 dark:text-amber-300";
  if (l.includes("INFO")) return "text-sky-600 dark:text-sky-300";
  if (l.includes("DEBUG")) return "text-muted-foreground";
  return "text-foreground";
}

function formatTime(ts: number) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [paused, setPaused] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [loading, setLoading] = useState(true);
  const lastTsRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { data: browser } = usePolling(api.getBrowserStatus, 3000);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (!active) return;
      try {
        const res = await api.getLogs(200, lastTsRef.current);
        if (!active) return;
        if (res.logs.length) {
          lastTsRef.current = res.logs[res.logs.length - 1].time;
          setLogs((prev) => {
            const next = [...prev, ...res.logs];
            if (next.length > 1000) return next.slice(-1000);
            return next;
          });
        }
      } catch {
        // ignore
      } finally {
        if (active) {
          setLoading(false);
          if (!paused) timer = setTimeout(tick, 3000);
        }
      }
    };

    if (!paused) tick();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [paused]);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  return (
    <div className="space-y-4">
      <VncViewer
        active={!!browser?.active}
        label={browser?.email ? `${browser.label} · ${browser.email}` : browser?.label}
        elapsed={browser?.elapsed_seconds}
        className="h-[320px] md:h-[400px]"
      />
      <Card className="flex flex-col h-[calc(100vh-560px)] min-h-[320px]">
      <div className="flex items-center justify-between px-5 py-3 border-b border-border/50">
        <div className="flex items-center gap-3">
          <div className="font-semibold">实时日志</div>
          {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          <span className="text-xs text-muted-foreground">{logs.length} 行</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs">
            <Switch checked={autoScroll} onCheckedChange={setAutoScroll} id="autoscroll" />
            <label htmlFor="autoscroll" className="text-muted-foreground">
              自动滚动
            </label>
          </div>
          <Button variant="subtle" size="sm" onClick={() => setPaused((p) => !p)}>
            {paused ? (
              <>
                <Play className="h-3.5 w-3.5" /> 继续
              </>
            ) : (
              <>
                <Pause className="h-3.5 w-3.5" /> 暂停
              </>
            )}
          </Button>
          <Button variant="subtle" size="sm" onClick={() => setLogs([])}>
            <Trash2 className="h-3.5 w-3.5" /> 清空
          </Button>
        </div>
      </div>
      <div
        ref={scrollRef}
        className="flex-1 overflow-auto scrollbar-thin font-mono text-[11px] leading-5 p-3 bg-background/40"
      >
        {logs.length === 0 && !loading && (
          <div className="text-center text-muted-foreground py-10">暂无日志</div>
        )}
        {logs.map((l, i) => (
          <div key={i} className="flex gap-3 hover:bg-accent/20 px-1 rounded">
            <span className="text-muted-foreground shrink-0">{formatTime(l.time)}</span>
            <span className={cn("shrink-0 w-14", levelColor(l.level))}>{l.level}</span>
            <span className="whitespace-pre-wrap break-all">{l.message}</span>
          </div>
        ))}
      </div>
      </Card>
    </div>
  );
}
