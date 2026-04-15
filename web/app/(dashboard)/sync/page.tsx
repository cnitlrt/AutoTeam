"use client";
import { ArrowDownToLine, ArrowUpFromLine, Database, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { ActionCard } from "@/components/feature/action-card";
import { toast } from "@/components/ui/sonner";

export default function SyncPage() {
  async function run<T>(fn: () => Promise<T>, success: string) {
    try {
      await fn();
      toast.success(success);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <ActionCard
        icon={ArrowUpFromLine}
        title="推送到 CPA"
        description="将本地 auths/ 推送到 CLIProxyAPI"
        accent="from-sky-500/20"
        buttonLabel="推送"
        onRun={() => run(() => api.postSync(), "已同步至 CPA")}
      />
      <ActionCard
        icon={ArrowDownToLine}
        title="从 CPA 拉取"
        description="把 CPA 中的 auth 文件拉到本地"
        accent="from-emerald-500/20"
        buttonLabel="拉取"
        onRun={() => run(() => api.postSyncFromCpa(), "已从 CPA 拉取")}
      />
      <ActionCard
        icon={Database}
        title="同步账号状态"
        description="合并本地 auths/ + Team 成员到 accounts.json"
        accent="from-indigo-500/20"
        buttonLabel="同步"
        onRun={() => run(() => api.postSyncAccounts(), "账号状态已更新")}
      />
      <ActionCard
        icon={ShieldCheck}
        title="主号 Codex 同步"
        description="完成主号登录后将其 auth 推送到 CPA"
        accent="from-violet-500/20"
        buttonLabel="同步主号"
        onRun={() => run(() => api.postSyncMainCodex(), "主号同步请求已提交")}
      />
    </div>
  );
}
