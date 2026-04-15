"use client";
import { CheckCircle2, PackagePlus, Recycle, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import { ActionCard } from "@/components/feature/action-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { toast } from "@/components/ui/sonner";

export default function PoolPage() {
  const { data: admin } = usePolling(api.getAdminStatus, 15000);
  const adminReady = !!admin?.configured && !admin?.login_in_progress;

  async function run<T>(fn: () => Promise<T>) {
    try {
      await fn();
      toast.success("任务已提交，可在任务历史中查看进度");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const disabledHint = adminReady ? undefined : "需要先在「设置」页完成管理员登录";

  return (
    <div className="space-y-6">
      {!adminReady && (
        <Alert variant="warning">
          <AlertDescription>
            账号池操作需要管理员 session。请前往「设置」页完成主号登录。
          </AlertDescription>
        </Alert>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <ActionCard
          icon={Recycle}
          title="智能轮转"
          description="检查配额，移除耗尽账号，从 standby 恢复或新建账号"
          accent="from-sky-500/20"
          buttonLabel="执行轮转"
          paramLabel="目标活跃数"
          paramPlaceholder="5"
          defaultParam="5"
          disabled={!adminReady}
          disabledHint={disabledHint}
          onRun={(v) => run(() => api.startRotate(Number(v) || 5))}
        />
        <ActionCard
          icon={CheckCircle2}
          title="配额检查"
          description="检查所有活跃账号的配额并标记耗尽"
          accent="from-emerald-500/20"
          buttonLabel="立即检查"
          disabled={!adminReady}
          disabledHint={disabledHint}
          onRun={() => run(() => api.startCheck())}
        />
        <ActionCard
          icon={RefreshCw}
          title="填充 Team"
          description="邀请 standby 或新建账号填到指定数量"
          accent="from-indigo-500/20"
          buttonLabel="填充"
          paramLabel="目标成员数"
          paramPlaceholder="5"
          defaultParam="5"
          disabled={!adminReady}
          disabledHint={disabledHint}
          onRun={(v) => run(() => api.startFill(Number(v) || 5))}
        />
        <ActionCard
          icon={PackagePlus}
          title="新增账号"
          description="注册一个新账号并加入 Team"
          accent="from-violet-500/20"
          buttonLabel="添加账号"
          disabled={!adminReady}
          disabledHint={disabledHint}
          onRun={() => run(() => api.startAdd())}
        />
        <ActionCard
          icon={Trash2}
          title="清理超额"
          description="移除 Team 中超过配额的成员"
          accent="from-rose-500/20"
          buttonLabel="清理"
          paramLabel="最大保留数（留空表示默认）"
          paramPlaceholder="5"
          disabled={!adminReady}
          disabledHint={disabledHint}
          onRun={(v) => run(() => api.startCleanup(v ? Number(v) : null))}
        />
      </div>
    </div>
  );
}
