"use client";
import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Download, LogIn, RefreshCw, Trash2, UserMinus } from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/sonner";
import type { CodexAuthExport, QuotaInfo } from "@/lib/types";
import { formatResetIn } from "@/lib/utils";
import { StatusBadge } from "@/components/feature/status-badge";
import { QuotaBar } from "@/components/feature/quota-bar";
import { SummaryCards } from "@/components/feature/summary-cards";

export default function DashboardPage() {
  const { data, loading, error, refresh } = usePolling(api.getStatus, 10000);
  const [exportData, setExportData] = useState<CodexAuthExport | null>(null);
  const [busyEmail, setBusyEmail] = useState<string | null>(null);

  const accounts = data?.accounts ?? [];

  async function doAction(email: string, type: "login" | "kick" | "delete" | "export") {
    setBusyEmail(email);
    try {
      if (type === "login") {
        await api.loginAccount(email);
        toast.success(`${email} 登录任务已提交`);
      } else if (type === "kick") {
        await api.kickAccount(email);
        toast.success(`${email} 已移出`);
      } else if (type === "delete") {
        if (!confirm(`确定要删除 ${email} 吗？`)) return;
        await api.deleteAccount(email);
        toast.success(`${email} 已删除`);
      } else if (type === "export") {
        const auth = await api.getCodexAuth(email);
        setExportData(auth);
      }
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusyEmail(null);
    }
  }

  async function syncAccounts() {
    try {
      const r = await api.postSyncAccounts();
      toast.success(r.message);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      {data && <SummaryCards summary={data.summary} />}
      {!data && loading && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
      )}

      <Card>
        <div className="flex items-center justify-between px-5 py-4 border-b border-border/50">
          <div>
            <div className="font-semibold">账号列表</div>
            <div className="text-xs text-muted-foreground mt-0.5">
              每 10 秒自动刷新配额
            </div>
          </div>
          <Button variant="subtle" size="sm" onClick={syncAccounts}>
            <RefreshCw className="h-3.5 w-3.5" />
            同步账号
          </Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">#</TableHead>
              <TableHead>邮箱</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="w-[180px]">5h 剩余</TableHead>
              <TableHead className="w-[180px]">周 剩余</TableHead>
              <TableHead>5h 重置</TableHead>
              <TableHead>周 重置</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && !data && (
              <TableRow>
                <TableCell colSpan={8}>
                  <div className="py-8 space-y-2">
                    <Skeleton className="h-4 w-full" />
                    <Skeleton className="h-4 w-3/4" />
                  </div>
                </TableCell>
              </TableRow>
            )}
            {error && !data && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-destructive py-8">
                  加载失败: {error.message}
                </TableCell>
              </TableRow>
            )}
            <AnimatePresence initial={false}>
              {accounts.map((acc, i) => {
                const q = (data?.quota_cache?.[acc.email] ?? acc.last_quota ?? {}) as QuotaInfo;
                const primaryRemain = q.primary_pct == null ? null : 100 - q.primary_pct;
                const weeklyRemain = q.weekly_pct == null ? null : 100 - q.weekly_pct;
                return (
                  <motion.tr
                    key={acc.email}
                    layout
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="border-b border-border/40 hover:bg-accent/30 transition-colors"
                  >
                    <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                    <TableCell className="font-mono text-xs">{acc.email}</TableCell>
                    <TableCell>
                      <StatusBadge status={acc.status} />
                    </TableCell>
                    <TableCell>
                      <QuotaBar pct={primaryRemain} />
                    </TableCell>
                    <TableCell>
                      <QuotaBar pct={weeklyRemain} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatResetIn(q.primary_resets_at)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatResetIn(q.weekly_resets_at)}
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        {acc.status !== "active" && (
                          <Button
                            size="xs"
                            variant="subtle"
                            disabled={busyEmail === acc.email}
                            onClick={() => doAction(acc.email, "login")}
                          >
                            <LogIn className="h-3 w-3" /> 登录
                          </Button>
                        )}
                        {acc.status === "active" && (
                          <>
                            <Button
                              size="xs"
                              variant="subtle"
                              disabled={busyEmail === acc.email}
                              onClick={() => doAction(acc.email, "kick")}
                            >
                              <UserMinus className="h-3 w-3" /> 移出
                            </Button>
                            <Button
                              size="xs"
                              variant="subtle"
                              disabled={busyEmail === acc.email}
                              onClick={() => doAction(acc.email, "export")}
                            >
                              <Download className="h-3 w-3" /> 导出
                            </Button>
                          </>
                        )}
                        <Button
                          size="xs"
                          variant="destructive"
                          disabled={busyEmail === acc.email}
                          onClick={() => doAction(acc.email, "delete")}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </TableCell>
                  </motion.tr>
                );
              })}
            </AnimatePresence>
            {data && accounts.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                  暂无账号
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      <CodexExportDialog data={exportData} onClose={() => setExportData(null)} />
    </div>
  );
}

function CodexExportDialog({
  data,
  onClose,
}: {
  data: CodexAuthExport | null;
  onClose: () => void;
}) {
  const json = data ? JSON.stringify(data.codex_auth, null, 2) : "";
  return (
    <Dialog open={!!data} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Codex CLI 认证文件</DialogTitle>
          <DialogDescription>
            将下方内容保存到 <code className="font-mono">~/.codex/auth.json</code>
            （Windows: <code className="font-mono">%APPDATA%\codex\auth.json</code>）
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border border-border/60 bg-muted/40 overflow-hidden">
          <pre className="p-4 max-h-[50vh] overflow-auto scrollbar-thin text-xs font-mono">
            {json}
          </pre>
        </div>
        {data?.hint && <p className="text-xs text-muted-foreground">{data.hint}</p>}
        <DialogFooter>
          <Button
            onClick={() => {
              navigator.clipboard.writeText(json);
              toast.success("已复制");
            }}
          >
            复制 JSON
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
