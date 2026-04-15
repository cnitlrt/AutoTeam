"use client";
import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronRight, CircleAlert, Loader2, Play, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDuration, formatRelative } from "@/lib/utils";
import type { TaskItem } from "@/lib/types";

function StatusBadge({ status }: { status: string }) {
  if (status === "completed")
    return (
      <Badge variant="success">
        <Check className="h-3 w-3" /> 完成
      </Badge>
    );
  if (status === "failed")
    return (
      <Badge variant="destructive">
        <CircleAlert className="h-3 w-3" /> 失败
      </Badge>
    );
  if (status === "running")
    return (
      <Badge variant="info">
        <Loader2 className="h-3 w-3 animate-spin" /> 运行中
      </Badge>
    );
  return (
    <Badge variant="muted">
      <Play className="h-3 w-3" /> 待开始
    </Badge>
  );
}

export default function TasksPage() {
  const { data, loading, refresh } = usePolling(api.getTasks, 5000);
  const [selected, setSelected] = useState<TaskItem | null>(null);

  return (
    <Card>
      <div className="flex items-center justify-between px-5 py-4 border-b border-border/50">
        <div>
          <div className="font-semibold">任务历史</div>
          <div className="text-xs text-muted-foreground mt-0.5">每 5 秒自动刷新</div>
        </div>
        <Button variant="subtle" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className="h-3.5 w-3.5" /> 刷新
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>命令</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>参数</TableHead>
            <TableHead>提交</TableHead>
            <TableHead>耗时</TableHead>
            <TableHead className="text-right"></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <AnimatePresence initial={false}>
            {(data || []).map((t) => (
              <motion.tr
                key={t.task_id}
                layout
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="border-b border-border/40 hover:bg-accent/30 cursor-pointer"
                onClick={() => setSelected(t)}
              >
                <TableCell className="font-mono text-xs">{t.command}</TableCell>
                <TableCell>
                  <StatusBadge status={t.status} />
                </TableCell>
                <TableCell className="text-xs text-muted-foreground font-mono max-w-[280px] truncate">
                  {Object.keys(t.params || {}).length
                    ? JSON.stringify(t.params)
                    : "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatRelative(t.created_at)}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDuration(t.started_at, t.finished_at)}
                </TableCell>
                <TableCell className="text-right">
                  <ChevronRight className="h-4 w-4 text-muted-foreground ml-auto" />
                </TableCell>
              </motion.tr>
            ))}
          </AnimatePresence>
          {data && data.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                暂无任务
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">{selected?.command}</DialogTitle>
          </DialogHeader>
          {selected && (
            <div className="space-y-3 text-sm">
              <div className="flex items-center gap-2">
                <StatusBadge status={selected.status} />
                <span className="text-xs text-muted-foreground">
                  ID: <code className="font-mono">{selected.task_id}</code>
                </span>
              </div>
              <DetailBlock label="参数">
                <pre className="text-xs font-mono whitespace-pre-wrap break-all">
                  {JSON.stringify(selected.params, null, 2)}
                </pre>
              </DetailBlock>
              {selected.result != null && (
                <DetailBlock label="结果">
                  <pre className="text-xs font-mono whitespace-pre-wrap break-all">
                    {JSON.stringify(selected.result, null, 2)}
                  </pre>
                </DetailBlock>
              )}
              {selected.error && (
                <DetailBlock label="错误" tone="destructive">
                  <pre className="text-xs font-mono whitespace-pre-wrap break-all">
                    {selected.error}
                  </pre>
                </DetailBlock>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function DetailBlock({
  label,
  children,
  tone,
}: {
  label: string;
  children: React.ReactNode;
  tone?: "destructive";
}) {
  return (
    <div>
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div
        className={`rounded-lg border p-3 max-h-60 overflow-auto scrollbar-thin ${
          tone === "destructive"
            ? "border-destructive/40 bg-destructive/10 text-destructive"
            : "border-border/60 bg-muted/40"
        }`}
      >
        {children}
      </div>
    </div>
  );
}
