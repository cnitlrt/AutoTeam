"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Rocket, Sparkles } from "lucide-react";
import { api, setApiKey } from "@/lib/api";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import type { SetupField, SetupStatus } from "@/lib/types";
import { toast } from "@/components/ui/sonner";

export default function SetupPage() {
  const { refresh } = useAuth();
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getSetupStatus()
      .then((s) => {
        setStatus(s);
        const init: Record<string, string> = {};
        s.fields.forEach((f) => (init[f.key] = f.default ?? ""));
        setValues(init);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.saveSetup(values);
      if (result.api_key) setApiKey(result.api_key);
      toast.success("配置已保存");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  if (!status) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Skeleton className="h-48 w-full max-w-lg" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 py-10">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="w-full max-w-2xl"
      >
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-500/30 to-sky-500/30 border border-emerald-500/20">
                <Rocket className="h-5 w-5 text-emerald-300" />
              </div>
              <div>
                <CardTitle>首次配置</CardTitle>
                <CardDescription>请填写 CloudMail 与 CPA 连接参数，系统会自动校验。</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-5">
              {status.fields.map((f) => (
                <FieldRow
                  key={f.key}
                  field={f}
                  value={values[f.key] ?? ""}
                  onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))}
                />
              ))}
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground flex items-center gap-1.5">
                  <Sparkles className="h-3 w-3" />
                  留空 API_KEY 系统会自动生成
                </p>
                <Button type="submit" disabled={submitting}>
                  {submitting ? "保存中..." : "保存并进入"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}

function FieldRow({
  field,
  value,
  onChange,
}: {
  field: SetupField;
  value: string;
  onChange: (v: string) => void;
}) {
  const isSecret = /KEY|PASSWORD|TOKEN/i.test(field.key);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label>
          {field.prompt}
          {!field.optional && <span className="text-destructive ml-1">*</span>}
        </Label>
        <span className="text-[10px] text-muted-foreground font-mono">{field.key}</span>
      </div>
      <Input
        type={isSecret ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.default || ""}
        required={!field.optional}
      />
    </div>
  );
}
