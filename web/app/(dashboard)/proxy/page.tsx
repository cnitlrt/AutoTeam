"use client";
import { useCallback, useState } from "react";
import { motion } from "framer-motion";
import {
  Globe,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "@/components/ui/sonner";
import type { ProxyEntry } from "@/lib/types";

function statusColor(status: string) {
  if (status === "good") return "bg-emerald-500";
  if (status === "slow") return "bg-amber-500";
  if (status === "bad") return "bg-rose-500";
  return "bg-muted-foreground/40";
}

function statusLabel(status: string) {
  if (status === "good") return "good";
  if (status === "slow") return "slow";
  if (status === "bad") return "bad";
  return "unchecked";
}

function formatLatency(ms: number | null) {
  if (ms == null) return "-";
  return `${Math.round(ms)}ms`;
}

function formatLastCheck(ts: number | null) {
  if (!ts) return "-";
  const ago = Math.round((Date.now() / 1000 - ts));
  if (ago < 60) return `${ago}s ago`;
  if (ago < 3600) return `${Math.floor(ago / 60)}m ago`;
  return `${Math.floor(ago / 3600)}h ago`;
}

export default function ProxyPage() {
  const { data, refresh, loading } = usePolling(api.getProxyConfig, 10000);
  const [bulkText, setBulkText] = useState("");
  const [adding, setAdding] = useState(false);
  const [intervalInput, setIntervalInput] = useState<string>("");

  const enabled = data?.enabled ?? false;
  const checkInterval = data?.check_interval ?? 60;
  const proxies = data?.proxies ?? [];

  const toggleEnabled = useCallback(async () => {
    try {
      await api.setProxyConfig({ enabled: !enabled });
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [enabled, refresh]);

  const saveInterval = useCallback(async () => {
    const val = parseInt(intervalInput || String(checkInterval));
    if (isNaN(val) || val < 10) {
      toast.error("检查间隔至少 10 秒");
      return;
    }
    try {
      await api.setProxyConfig({ check_interval: val });
      setIntervalInput("");
      toast.success("已保存");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [intervalInput, checkInterval, refresh]);

  async function addBulk(e: React.FormEvent) {
    e.preventDefault();
    if (!bulkText.trim()) return;
    setAdding(true);
    try {
      const r = await api.addProxies(bulkText.trim());
      toast.success(`已添加 ${r.added} 个代理${r.skipped ? `，跳过 ${r.skipped} 个重复` : ""}`);
      setBulkText("");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setAdding(false);
    }
  }

  async function remove(p: ProxyEntry) {
    try {
      await api.deleteProxy(p.id);
      toast.success("已删除");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function removeAll() {
    if (!confirm(`确定要删除全部 ${proxies.length} 个代理？`)) return;
    try {
      await api.deleteAllProxies();
      toast.success("已全部删除");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function triggerCheck() {
    try {
      await api.checkProxies();
      toast.success("健康检查已触发");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const goodCount = proxies.filter((p) => p.status === "good").length;
  const slowCount = proxies.filter((p) => p.status === "slow").length;
  const badCount = proxies.filter((p) => p.status === "bad").length;

  return (
    <div className="space-y-6">
      <Card className="p-5">
        <div className="flex items-start justify-between mb-4">
          <div>
            <div className="font-semibold flex items-center gap-2">
              <Globe className="h-4 w-4" /> 代理设置
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              启用后，所有任务（包括浏览器）将通过代理运行。同一任务使用同一代理，不同任务可使用不同代理。
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Label htmlFor="proxy-toggle" className="text-sm">
              {enabled ? "已启用" : "已禁用"}
            </Label>
            <Switch id="proxy-toggle" checked={enabled} onCheckedChange={toggleEnabled} />
          </div>
        </div>

        <div className="flex items-end gap-3">
          <div className="space-y-1.5 w-48">
            <Label className="text-xs text-muted-foreground">健康检查间隔（秒）</Label>
            <Input
              type="number"
              min={10}
              placeholder={String(checkInterval)}
              value={intervalInput}
              onChange={(e) => setIntervalInput(e.target.value)}
            />
          </div>
          <Button
            variant="subtle"
            size="sm"
            onClick={saveInterval}
            disabled={!intervalInput}
          >
            保存
          </Button>
        </div>

        {proxies.length > 0 && (
          <div className="flex items-center gap-4 mt-4 text-xs text-muted-foreground">
            <span>共 {proxies.length} 个</span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-emerald-500" /> {goodCount} good
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-amber-500" /> {slowCount} slow
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-rose-500" /> {badCount} bad
            </span>
          </div>
        )}
      </Card>

      <Card className="p-5">
        <div className="flex items-center gap-2 mb-4">
          <Plus className="h-4 w-4" />
          <div className="font-semibold">批量添加代理</div>
        </div>
        <form onSubmit={addBulk} className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">
              格式：<code className="font-mono">IP:端口:用户名:密码</code>（每行一个，重复自动跳过）
            </Label>
            <textarea
              className="flex min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y"
              placeholder={"130.43.184.254:10354:user:pass\n192.168.1.1:8080:user2:pass2"}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              rows={6}
            />
          </div>
          <Button type="submit" disabled={adding || !bulkText.trim()}>
            {adding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
            添加
          </Button>
        </form>
      </Card>

      <Card>
        <div className="flex items-center justify-between px-5 py-4 border-b border-border/50">
          <div>
            <div className="font-semibold">代理列表</div>
            <div className="text-xs text-muted-foreground mt-0.5">
              每 {checkInterval} 秒自动检查连通性
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="subtle" size="sm" onClick={triggerCheck}>
              <RefreshCw className="h-3.5 w-3.5" /> 检查
            </Button>
            <Button variant="subtle" size="sm" onClick={refresh} disabled={loading}>
              <RefreshCw className="h-3.5 w-3.5" /> 刷新
            </Button>
            {proxies.length > 0 && (
              <Button variant="destructive" size="sm" onClick={removeAll}>
                <Trash2 className="h-3.5 w-3.5" /> 全部删除
              </Button>
            )}
          </div>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">状态</TableHead>
              <TableHead>地址</TableHead>
              <TableHead>用户名</TableHead>
              <TableHead>延迟</TableHead>
              <TableHead>最近检查</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {proxies.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground py-10">
                  还没有代理 — 在上方批量添加。
                </TableCell>
              </TableRow>
            )}
            {proxies.map((p) => (
              <motion.tr
                key={p.id}
                layout
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="border-b border-border/40 hover:bg-accent/20"
              >
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className={`h-2.5 w-2.5 rounded-full ${statusColor(p.status)}`} />
                    <span className="text-xs text-muted-foreground">{statusLabel(p.status)}</span>
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {p.host}:{p.port}
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  {p.username || "-"}
                </TableCell>
                <TableCell className="text-xs">
                  <span
                    className={
                      p.status === "good"
                        ? "text-emerald-600 dark:text-emerald-400"
                        : p.status === "slow"
                          ? "text-amber-600 dark:text-amber-400"
                          : p.status === "bad"
                            ? "text-rose-600 dark:text-rose-400"
                            : "text-muted-foreground"
                    }
                  >
                    {formatLatency(p.latency_ms)}
                  </span>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatLastCheck(p.last_check)}
                </TableCell>
                <TableCell>
                  <div className="flex justify-end">
                    <Button size="xs" variant="destructive" onClick={() => remove(p)}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </TableCell>
              </motion.tr>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
