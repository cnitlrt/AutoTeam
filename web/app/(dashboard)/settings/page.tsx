"use client";
import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Database, Download, Loader2, LogOut, Save, ShieldCheck, Upload, UserCircle } from "lucide-react";
import { api, getApiKey, setApiKey } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "@/components/ui/sonner";
import type { AdminStatus, AutoCheckConfig } from "@/lib/types";

export default function SettingsPage() {
  const { data: admin, refresh: refreshAdmin } = usePolling(api.getAdminStatus, 4000);
  const { data: codex, refresh: refreshCodex } = usePolling(api.getMainCodexStatus, 4000);

  return (
    <div className="space-y-6 max-w-4xl">
      <AdminCard status={admin} refresh={refreshAdmin} />
      {admin?.configured && <MainCodexCard status={codex} refresh={refreshCodex} />}
      <AutoCheckCard />
      <BackupCard />
    </div>
  );
}

function BackupCard() {
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  function downloadBackup() {
    const key = getApiKey();
    const url = `/api/backup/export${key ? `?key=${encodeURIComponent(key)}` : ""}`;
    // Use a hidden anchor to trigger the browser download (preserves the
    // server-suggested filename via Content-Disposition).
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    toast.success("正在下载备份");
  }

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!confirm(`确定要从 ${file.name} 导入备份？将覆盖当前所有账号、配置、SMS、代理与 auth 文件。`)) {
      e.target.value = "";
      return;
    }
    setImporting(true);
    try {
      const text = await file.text();
      const bundle = JSON.parse(text);
      const r = await api.importBackup(bundle);
      if (r.api_key) setApiKey(r.api_key);
      const c = r.imported;
      toast.success(
        `导入完成 — env ${c.env_keys}, 账号 ${c.accounts}, SMS ${c.sms_providers}, 代理 ${c.proxies}, auth ${c.auth_files}`,
      );
      // Reload so all polling hooks pick up the new state
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      toast.error(`导入失败：${(err as Error).message}`);
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <Card className="p-5">
      <div className="mb-4">
        <div className="font-semibold flex items-center gap-2">
          <Database className="h-4 w-4" /> 备份 / 迁移
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          一键导出全部状态（.env、账号、SMS、代理、auth 文件）为单个 JSON。
          在新服务器的首次配置页或本页可上传该 JSON 完成迁移。
        </p>
      </div>
      <div className="flex flex-col sm:flex-row gap-2">
        <Button variant="subtle" onClick={downloadBackup}>
          <Download className="h-3.5 w-3.5" /> 导出备份
        </Button>
        <Button variant="subtle" disabled={importing} onClick={() => fileRef.current?.click()}>
          {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
          导入备份
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          onChange={handleFile}
          className="hidden"
        />
      </div>
      <Alert variant="warning" className="mt-3">
        <AlertDescription className="text-xs">
          导入会<strong>完全覆盖</strong>当前实例的所有配置与账号数据，请先导出本地备份再操作。
        </AlertDescription>
      </Alert>
    </Card>
  );
}

function AdminCard({
  status,
  refresh,
}: {
  status: AdminStatus | null;
  refresh: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [session, setSession] = useState("");
  const [busy, setBusy] = useState(false);

  const configured = !!status?.configured;
  const inProgress = !!status?.login_in_progress;
  const step = status?.login_step;
  const sessionEmail = status?.email ?? "";

  async function wrap(fn: () => Promise<unknown>, msg?: string) {
    setBusy(true);
    try {
      await fn();
      if (msg) toast.success(msg);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="font-semibold flex items-center gap-2">
            <UserCircle className="h-4 w-4" /> 管理员登录
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            保存主号 session / workspace，供 Team 管理和 Codex 同步使用。
          </p>
        </div>
        <Badge variant={configured ? "success" : inProgress ? "warning" : "muted"}>
          {configured ? "已配置" : inProgress ? "登录中" : "未配置"}
        </Badge>
      </div>

      {configured && !inProgress && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4 text-sm"
        >
          <InfoBox label="管理员邮箱" value={status?.email} mono />
          <InfoBox label="Workspace ID" value={status?.account_id} mono />
          <InfoBox
            label="Workspace 名称"
            value={status?.workspace_name || "未识别"}
            className="sm:col-span-2"
          />
          <InfoBox
            label="Session Token"
            value={status?.session_present ? "已配置" : "未配置"}
            tone={status?.session_present ? "success" : "warning"}
            className="sm:col-span-2"
          />
          <InfoBox
            label="管理员密码"
            value={status?.password_saved ? "已保存（可用于 Codex 同步）" : "未保存"}
            className="sm:col-span-2"
          />
        </motion.div>
      )}

      {!configured && !inProgress && (
        <div className="flex flex-col sm:flex-row gap-2">
          <Input
            placeholder="主号邮箱"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="flex-1"
          />
          <Button
            onClick={() => wrap(() => api.startAdminLogin(email.trim()))}
            disabled={busy || !email.trim()}
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            启动登录
          </Button>
        </div>
      )}

      {!configured && !inProgress && (
        <div className="mt-4 space-y-2">
          <Label className="text-xs text-muted-foreground">或直接导入 session token</Label>
          <div className="flex gap-2">
            <Input
              placeholder="管理员邮箱"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="flex-1"
            />
            <PasswordInput
              placeholder="session token"
              value={session}
              onChange={(e) => setSession(e.target.value)}
              className="flex-[2]"
            />
            <Button
              variant="subtle"
              disabled={busy || !email.trim() || !session.trim()}
              onClick={() =>
                wrap(() => api.submitAdminSession(email.trim(), session.trim()), "session 已保存")
              }
            >
              导入
            </Button>
          </div>
        </div>
      )}

      {inProgress && step === "password_required" && (
        <FormRow label="请输入管理员密码">
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="flex-1"
          />
          <Button
            disabled={busy || !password}
            onClick={() => wrap(() => api.submitAdminPassword(password))}
          >
            提交
          </Button>
        </FormRow>
      )}

      {inProgress && step === "code_required" && (
        <FormRow label="请输入验证码（邮件）">
          <Input value={code} onChange={(e) => setCode(e.target.value)} />
          <Button disabled={busy || !code} onClick={() => wrap(() => api.submitAdminCode(code))}>
            提交
          </Button>
        </FormRow>
      )}

      {inProgress && step === "workspace_required" && (
        <FormRow label="选择 workspace">
          <Select value={workspace} onValueChange={setWorkspace}>
            <SelectTrigger className="flex-1">
              <SelectValue placeholder="选择..." />
            </SelectTrigger>
            <SelectContent>
              {(status?.workspace_options ?? []).map((opt) => (
                <SelectItem key={opt.id} value={opt.id}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            disabled={busy || !workspace}
            onClick={() => wrap(() => api.submitAdminWorkspace(workspace))}
          >
            确认
          </Button>
        </FormRow>
      )}

      {inProgress && (
        <div className="mt-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => wrap(() => api.cancelAdminLogin(), "已取消")}
          >
            取消登录
          </Button>
        </div>
      )}

      {configured && (
        <div className="mt-4">
          <Button
            variant="subtle"
            size="sm"
            onClick={() => {
              if (!confirm("清除管理员 session？")) return;
              wrap(() => api.logoutAdmin(), "已清除");
            }}
          >
            <LogOut className="h-3.5 w-3.5" /> 清除 session
          </Button>
        </div>
      )}
    </Card>
  );
}

function MainCodexCard({
  status,
  refresh,
}: {
  status: { in_progress: boolean; step?: string | null } | null;
  refresh: () => void;
}) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  async function wrap(fn: () => Promise<unknown>, msg?: string) {
    setBusy(true);
    try {
      await fn();
      if (msg) toast.success(msg);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between mb-4">
        <div>
          <div className="font-semibold flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" /> 主号 Codex 同步
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            登录主号 Codex 并将 auth 文件推送到 CPA。
          </p>
        </div>
        <Badge variant={status?.in_progress ? "warning" : "muted"}>
          {status?.in_progress ? "进行中" : "空闲"}
        </Badge>
      </div>

      {!status?.in_progress && (
        <Button
          disabled={busy}
          onClick={() => wrap(() => api.startMainCodexSync(), "主号同步已启动")}
        >
          启动同步
        </Button>
      )}

      {status?.in_progress && status.step === "password_required" && (
        <FormRow label="主号密码">
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="flex-1"
          />
          <Button
            disabled={busy || !password}
            onClick={() => wrap(() => api.submitMainCodexPassword(password))}
          >
            提交
          </Button>
        </FormRow>
      )}
      {status?.in_progress && status.step === "code_required" && (
        <FormRow label="Codex 验证码">
          <Input value={code} onChange={(e) => setCode(e.target.value)} />
          <Button
            disabled={busy || !code}
            onClick={() => wrap(() => api.submitMainCodexCode(code))}
          >
            提交
          </Button>
        </FormRow>
      )}
      {status?.in_progress && (
        <div className="mt-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => wrap(() => api.cancelMainCodexSync(), "已取消")}
          >
            取消
          </Button>
        </div>
      )}
    </Card>
  );
}

function AutoCheckCard() {
  const [cfg, setCfg] = useState<AutoCheckConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLoading(true);
    api
      .getAutoCheckConfig()
      .then(setCfg)
      .catch((e) => toast.error((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    if (!cfg) return;
    setSaving(true);
    try {
      const result = await api.setAutoCheckConfig(cfg);
      setCfg(result);
      toast.success("已保存");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="mb-4">
        <div className="font-semibold">后台自动检查</div>
        <p className="text-xs text-muted-foreground mt-1">
          API 模式下每间隔 N 秒检查账号配额，满足阈值时自动触发轮转。
        </p>
      </div>
      {!cfg && loading ? (
        <div className="text-xs text-muted-foreground">加载中...</div>
      ) : cfg ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="space-y-1.5">
            <Label>检查间隔（秒）</Label>
            <Input
              type="number"
              value={cfg.interval}
              onChange={(e) => setCfg({ ...cfg, interval: Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>低配额阈值 (%)</Label>
            <Input
              type="number"
              value={cfg.threshold}
              onChange={(e) => setCfg({ ...cfg, threshold: Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>触发最小低配数</Label>
            <Input
              type="number"
              value={cfg.min_low}
              onChange={(e) => setCfg({ ...cfg, min_low: Number(e.target.value) })}
            />
          </div>
          <div className="sm:col-span-3">
            <Button disabled={saving} onClick={save}>
              <Save className="h-3.5 w-3.5" />
              {saving ? "保存中..." : "保存"}
            </Button>
          </div>
        </div>
      ) : (
        <Alert variant="destructive">
          <AlertDescription>无法加载配置</AlertDescription>
        </Alert>
      )}
    </Card>
  );
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-3 space-y-2"
    >
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex gap-2">{children}</div>
    </motion.div>
  );
}

function InfoBox({
  label,
  value,
  mono,
  tone,
  className,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
  tone?: "success" | "warning";
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-border/60 bg-background/40 px-3 py-2 ${className ?? ""}`}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={`mt-0.5 text-sm break-all ${mono ? "font-mono text-xs" : ""} ${
          tone === "success"
            ? "text-emerald-700 dark:text-emerald-300"
            : tone === "warning"
              ? "text-amber-700 dark:text-amber-300"
              : ""
        }`}
      >
        {value || "-"}
      </div>
    </div>
  );
}
