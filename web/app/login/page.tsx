"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { KeyRound, Sparkles } from "lucide-react";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";

export default function LoginPage() {
  const { state, login } = useAuth();
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (state === "authenticated") {
      // AuthProvider handles redirect
    }
  }, [state]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!key.trim()) return;
    setLoading(true);
    setError(null);
    const ok = await login(key.trim());
    setLoading(false);
    if (!ok) setError("API Key 无效，请检查后重试。");
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="w-full max-w-md"
      >
        <Card className="border-border/60">
          <CardHeader className="items-center text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-sky-500/30 to-indigo-500/30 border border-sky-500/20">
              <Sparkles className="h-5 w-5 text-sky-300" />
            </div>
            <CardTitle className="text-xl">AutoTeam 控制台</CardTitle>
            <CardDescription>请输入 API Key 登录</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="apikey">API Key</Label>
                <div className="relative">
                  <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground z-10" />
                  <PasswordInput
                    id="apikey"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder="粘贴 API Key"
                    autoFocus
                    className="pl-9"
                  />
                </div>
              </div>
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <Button type="submit" disabled={loading || !key.trim()} className="w-full">
                {loading ? "验证中..." : "登录"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
