"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { Check, Copy, ExternalLink, Loader2, Play, Power, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { toast } from "@/components/ui/sonner";

export default function OAuthPage() {
  const { data: status, loading, refresh } = usePolling(api.getManualAccountStatus, 3000);
  const [callbackUrl, setCallbackUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const inProgress = status?.in_progress;
  const authUrl = status?.auth_url;
  const completed = status?.status === "completed";
  const errored = status?.status === "error";

  async function startFlow() {
    try {
      await api.startManualAccount();
      toast.success("授权链接已生成");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function submitCallback() {
    if (!callbackUrl.trim()) return;
    setSubmitting(true);
    try {
      await api.submitManualAccountCallback(callbackUrl.trim());
      toast.success("回调已处理");
      setCallbackUrl("");
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel() {
    try {
      await api.cancelManualAccount();
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div className="space-y-4 max-w-3xl">
      <Card className="p-5">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <div className="font-semibold">手动 OAuth 导入</div>
            <p className="text-xs text-muted-foreground mt-1">
              生成授权链接，浏览器登录后将回调 URL 粘贴回来完成导入。
            </p>
          </div>
          <StatusPill status={status?.status} inProgress={!!inProgress} />
        </div>

        {status?.auto_callback_available && (
          <Alert variant="info" className="mb-4">
            <AlertDescription>
              检测到 localhost:1455 自动回调端口已就绪，登录后会自动捕获。
            </AlertDescription>
          </Alert>
        )}
        {status?.auto_callback_error && (
          <Alert variant="warning" className="mb-4">
            <AlertDescription>自动回调不可用：{status.auto_callback_error}</AlertDescription>
          </Alert>
        )}
        {errored && (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{status?.error || "OAuth 流程失败"}</AlertDescription>
          </Alert>
        )}

        {!inProgress && !completed && (
          <div className="flex gap-2">
            <Button onClick={startFlow} disabled={loading}>
              <Play className="h-3.5 w-3.5" /> 启动流程
            </Button>
            <Button variant="ghost" size="sm" onClick={refresh}>
              <RefreshCw className="h-3.5 w-3.5" /> 刷新
            </Button>
          </div>
        )}

        {authUrl && inProgress && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-4"
          >
            <div className="space-y-1.5">
              <Label>授权链接</Label>
              <div className="flex items-center gap-2">
                <Input value={authUrl} readOnly className="font-mono text-xs" />
                <Button
                  variant="subtle"
                  size="icon"
                  onClick={() => {
                    navigator.clipboard.writeText(authUrl);
                    toast.success("已复制");
                  }}
                >
                  <Copy className="h-3.5 w-3.5" />
                </Button>
                <Button asChild variant="subtle" size="icon">
                  <a href={authUrl} target="_blank" rel="noreferrer">
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </Button>
              </div>
            </div>

            {status?.callback_received ? (
              <Alert variant="success">
                <AlertDescription>
                  已接收回调（{status.callback_source === "auto" ? "自动" : "手动"}），正在处理……
                </AlertDescription>
              </Alert>
            ) : (
              <div className="space-y-1.5">
                <Label>回调 URL（手动粘贴）</Label>
                <div className="flex gap-2">
                  <Input
                    placeholder="http://localhost:1455/auth/callback?code=..."
                    value={callbackUrl}
                    onChange={(e) => setCallbackUrl(e.target.value)}
                  />
                  <Button disabled={submitting || !callbackUrl.trim()} onClick={submitCallback}>
                    {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "提交"}
                  </Button>
                </div>
              </div>
            )}

            <Button variant="ghost" size="sm" onClick={cancel}>
              <Power className="h-3.5 w-3.5" /> 取消流程
            </Button>
          </motion.div>
        )}

        {completed && (
          <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex items-center gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 text-emerald-700 dark:text-emerald-300"
          >
            <Check className="h-5 w-5" />
            <div>
              <div className="font-medium">导入完成</div>
              {status?.account?.email && (
                <div className="text-xs opacity-80 mt-0.5 font-mono">{status.account.email}</div>
              )}
            </div>
            <Button variant="ghost" size="sm" className="ml-auto" onClick={startFlow}>
              再导入一个
            </Button>
          </motion.div>
        )}
      </Card>
    </div>
  );
}

function StatusPill({ status, inProgress }: { status?: string; inProgress: boolean }) {
  if (!status || status === "idle") return <Badge variant="muted">空闲</Badge>;
  if (status === "completed") return <Badge variant="success">已完成</Badge>;
  if (status === "error") return <Badge variant="destructive">错误</Badge>;
  if (inProgress) return <Badge variant="warning">进行中</Badge>;
  return <Badge variant="muted">{status}</Badge>;
}
