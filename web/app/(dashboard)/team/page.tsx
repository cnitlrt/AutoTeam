"use client";
import { AnimatePresence, motion } from "framer-motion";
import { RefreshCw, UserX } from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "@/components/ui/sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { TeamMember } from "@/lib/types";
import { useState } from "react";

function roleVariant(role: string) {
  if (role === "account-owner") return "warning" as const;
  if (role === "account-admin") return "info" as const;
  return "muted" as const;
}

function roleLabel(role: string) {
  const map: Record<string, string> = {
    "account-owner": "Owner",
    "account-admin": "Admin",
    member: "Member",
  };
  return map[role] || role;
}

export default function TeamPage() {
  const { data, loading, error, refresh } = usePolling(api.getTeamMembers, 60000);
  const [busy, setBusy] = useState<string | null>(null);

  async function remove(m: TeamMember) {
    const label = m.type === "invite" ? "取消邀请" : "移除";
    if (!confirm(`${label} ${m.email}？`)) return;
    setBusy(m.email);
    try {
      await api.removeTeamMember({ email: m.email, user_id: m.user_id, type: m.type });
      toast.success(`${m.email} ${label}成功`);
      refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <div className="flex items-center justify-between px-5 py-4 border-b border-border/50">
        <div>
          <div className="font-semibold">ChatGPT Team 成员</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            {data && (
              <>
                共 {data.total} 位成员 · {data.invites} 个待接受邀请
              </>
            )}
          </div>
        </div>
        <Button variant="subtle" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className="h-3.5 w-3.5" /> 刷新
        </Button>
      </div>
      {error && (
        <div className="px-5 pt-4">
          <Alert variant="destructive">
            <AlertDescription>{error.message}</AlertDescription>
          </Alert>
        </div>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead>邮箱</TableHead>
            <TableHead>角色</TableHead>
            <TableHead>类型</TableHead>
            <TableHead>来源</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {!data && loading && (
            <>
              {Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={6}>
                    <Skeleton className="h-6 w-full" />
                  </TableCell>
                </TableRow>
              ))}
            </>
          )}
          <AnimatePresence initial={false}>
            {data?.members.map((m, i) => (
              <motion.tr
                key={m.email + m.type}
                layout
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="border-b border-border/40 hover:bg-accent/30"
              >
                <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                <TableCell className="font-mono text-xs">{m.email}</TableCell>
                <TableCell>
                  <Badge variant={roleVariant(m.role)}>{roleLabel(m.role)}</Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={m.type === "invite" ? "warning" : "muted"}>
                    {m.type === "invite" ? "邀请中" : "成员"}
                  </Badge>
                </TableCell>
                <TableCell>
                  {m.is_local ? (
                    <Badge variant="info">本地</Badge>
                  ) : (
                    <Badge variant="muted">外部</Badge>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex justify-end">
                    {m.role === "account-owner" ? (
                      <Badge variant="muted">不可移除</Badge>
                    ) : (
                      <Button
                        size="xs"
                        variant="subtle"
                        disabled={busy === m.email}
                        onClick={() => remove(m)}
                      >
                        <UserX className="h-3 w-3" />
                        {m.type === "invite" ? "取消" : "移除"}
                      </Button>
                    )}
                  </div>
                </TableCell>
              </motion.tr>
            ))}
          </AnimatePresence>
          {data && data.members.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                暂无成员
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Card>
  );
}
