"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  CircleAlert,
  Edit2,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PasswordInput } from "@/components/ui/password-input";
import { toast } from "@/components/ui/sonner";
import type { SMSProvider } from "@/lib/types";

export default function SmsPage() {
  const { data, refresh, loading } = usePolling(api.getSmsProviders, 15000);
  const providers = data?.providers ?? [];
  const types = data?.available_types ?? [];
  const defaultService = data?.default_service ?? "chatgpt";

  return (
    <div className="space-y-6">
      <ProvidersList
        providers={providers}
        loading={loading}
        onChange={refresh}
        defaultService={defaultService}
      />
      <AddProviderCard types={types} onChange={refresh} />
    </div>
  );
}

// ---------------- Providers list ----------------

function ProvidersList({
  providers,
  loading,
  onChange,
  defaultService,
}: {
  providers: SMSProvider[];
  loading: boolean;
  onChange: () => void;
  defaultService: string;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editKey, setEditKey] = useState("");
  const [editLabel, setEditLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [chatgptPrices, setChatgptPrices] = useState<Record<string, string | number>>({});

  useEffect(() => {
    for (const p of providers) {
      if (p.enabled && !chatgptPrices[p.id]) {
        api
          .getSmsProviderServices(p.id)
          .then((r) => {
            const svc = r.services.find(
              (s) =>
                (s.api_name || "").toLowerCase() === "chatgpt" ||
                (s.service_name || "").toLowerCase().includes("chatgpt"),
            );
            if (svc) {
              setChatgptPrices((prev) => ({ ...prev, [p.id]: svc.price ?? "-" }));
            }
          })
          .catch(() => {});
      }
    }
  }, [providers, chatgptPrices]);

  async function move(id: string, dir: -1 | 1) {
    const idx = providers.findIndex((p) => p.id === id);
    if (idx < 0) return;
    const next = idx + dir;
    if (next < 0 || next >= providers.length) return;
    const order = providers.map((p) => p.id);
    [order[idx], order[next]] = [order[next], order[idx]];
    try {
      await api.reorderSmsProviders(order);
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function toggle(p: SMSProvider) {
    try {
      await api.updateSmsProvider(p.id, { enabled: !p.enabled });
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function remove(p: SMSProvider) {
    if (!confirm(`删除 ${p.label}？`)) return;
    try {
      await api.deleteSmsProvider(p.id);
      toast.success("已删除");
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function test(p: SMSProvider) {
    try {
      const r = await api.testSmsProvider(p.id);
      toast.success(`余额 $${r.balance.toFixed(2)}`);
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  function startEdit(p: SMSProvider) {
    setEditingId(p.id);
    setEditKey("");
    setEditLabel(p.label);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditKey("");
    setEditLabel("");
  }

  async function saveEdit(p: SMSProvider) {
    setSaving(true);
    try {
      const params: { api_key?: string; label?: string } = {};
      if (editKey.trim()) params.api_key = editKey.trim();
      if (editLabel.trim() !== p.label) params.label = editLabel.trim();
      if (Object.keys(params).length === 0) {
        cancelEdit();
        return;
      }
      await api.updateSmsProvider(p.id, params);
      toast.success("已保存");
      cancelEdit();
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <div className="flex items-center justify-between px-5 py-4 border-b border-border/50">
        <div>
          <div className="font-semibold">已配置的提供商</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            列表顺序即优先级 — 顶部的提供商优先使用，不可用时自动 fallback。默认 service:{" "}
            <code className="font-mono">{defaultService}</code>
          </div>
        </div>
        <Button variant="subtle" size="sm" onClick={onChange} disabled={loading}>
          <RefreshCw className="h-3.5 w-3.5" /> 刷新
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead>标签</TableHead>
            <TableHead>类型</TableHead>
            <TableHead>ChatGPT 价格</TableHead>
            <TableHead>余额</TableHead>
            <TableHead>启用</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {providers.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground py-10">
                还没有 SMS 提供商 — 在下方添加一个。
              </TableCell>
            </TableRow>
          )}
          {providers.map((p, i) => (
            <motion.tr
              key={p.id}
              layout
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="border-b border-border/40 hover:bg-accent/20"
            >
              <TableCell className="text-muted-foreground">{i + 1}</TableCell>
              <TableCell>
                {editingId === p.id ? (
                  <Input
                    value={editLabel}
                    onChange={(e) => setEditLabel(e.target.value)}
                    className="h-7 w-32 text-sm"
                  />
                ) : (
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{p.label}</span>
                    {i === 0 && p.enabled && <Badge variant="info">优先</Badge>}
                  </div>
                )}
              </TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">{p.type}</TableCell>
              <TableCell className="text-xs">
                {chatgptPrices[p.id] != null ? (
                  <span className="font-mono">${chatgptPrices[p.id]}</span>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell>
                {p.error ? (
                  <span className="inline-flex items-center gap-1 text-xs text-rose-500">
                    <CircleAlert className="h-3 w-3" /> {p.error}
                  </span>
                ) : p.balance != null ? (
                  <span
                    className={`font-mono ${p.balance < 1 ? "text-amber-500" : "text-emerald-500"}`}
                  >
                    ${p.balance.toFixed(2)}
                  </span>
                ) : (
                  <span className="text-muted-foreground text-xs">-</span>
                )}
              </TableCell>
              <TableCell>
                <Switch checked={p.enabled} onCheckedChange={() => toggle(p)} />
              </TableCell>
              <TableCell>
                {editingId === p.id ? (
                  <div className="flex justify-end gap-1">
                    <PasswordInput
                      value={editKey}
                      onChange={(e) => setEditKey(e.target.value)}
                      placeholder="新 API Key（留空不改）"
                      className="h-7 w-44 text-xs"
                    />
                    <Button
                      size="xs"
                      variant="subtle"
                      onClick={() => saveEdit(p)}
                      disabled={saving}
                    >
                      {saving ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Save className="h-3 w-3" />
                      )}
                    </Button>
                    <Button size="xs" variant="ghost" onClick={cancelEdit}>
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                ) : (
                  <div className="flex justify-end gap-1">
                    <Button size="xs" variant="ghost" disabled={i === 0} onClick={() => move(p.id, -1)}>
                      <ArrowUp className="h-3 w-3" />
                    </Button>
                    <Button
                      size="xs"
                      variant="ghost"
                      disabled={i === providers.length - 1}
                      onClick={() => move(p.id, 1)}
                    >
                      <ArrowDown className="h-3 w-3" />
                    </Button>
                    <Button size="xs" variant="subtle" onClick={() => startEdit(p)}>
                      <Edit2 className="h-3 w-3" />
                    </Button>
                    <Button size="xs" variant="subtle" onClick={() => test(p)}>
                      测试
                    </Button>
                    <Button size="xs" variant="destructive" onClick={() => remove(p)}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                )}
              </TableCell>
            </motion.tr>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

// ---------------- Add provider ----------------

function AddProviderCard({
  types,
  onChange,
}: {
  types: { type: string; name: string; api_key_label: string; help?: string }[];
  onChange: () => void;
}) {
  const [type, setType] = useState(types[0]?.type ?? "getatext");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!type && types[0]) setType(types[0].type);
  }, [types, type]);

  const current = types.find((t) => t.type === type);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.addSmsProvider({ type, api_key: apiKey.trim(), label: label.trim() });
      toast.success("已添加");
      setApiKey("");
      setLabel("");
      onChange();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-center gap-2 mb-4">
        <Plus className="h-4 w-4" />
        <div className="font-semibold">添加提供商</div>
      </div>
      <form onSubmit={submit} className="grid grid-cols-1 sm:grid-cols-[160px_1fr_1fr_auto] gap-3 items-end">
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">类型</Label>
          <Select value={type} onValueChange={setType}>
            <SelectTrigger>
              <SelectValue placeholder="选择..." />
            </SelectTrigger>
            <SelectContent>
              {types.map((t) => (
                <SelectItem key={t.type} value={t.type}>
                  {t.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">标签（可选）</Label>
          <Input
            placeholder={current?.name || ""}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">
            {current?.api_key_label ?? "API Key"}
          </Label>
          <PasswordInput
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            required
            placeholder={current?.help || "API key"}
          />
        </div>
        <Button type="submit" disabled={submitting || !apiKey.trim()}>
          {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          添加
        </Button>
      </form>
      {current?.help && (
        <p className="text-xs text-muted-foreground mt-2">{current.help}</p>
      )}
    </Card>
  );
}
