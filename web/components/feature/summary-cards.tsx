"use client";
import { motion } from "framer-motion";
import { Activity, AlertTriangle, Clock, Users } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface SummaryProps {
  summary: {
    active: number;
    standby: number;
    exhausted: number;
    pending: number;
    total: number;
  };
}

const CARDS = [
  {
    key: "active",
    label: "激活",
    icon: Activity,
    color: "text-emerald-600 dark:text-emerald-400",
    glow: "from-emerald-500/20",
  },
  {
    key: "standby",
    label: "待用",
    icon: Users,
    color: "text-sky-600 dark:text-sky-400",
    glow: "from-sky-500/20",
  },
  {
    key: "exhausted",
    label: "耗尽",
    icon: AlertTriangle,
    color: "text-amber-600 dark:text-amber-400",
    glow: "from-amber-500/20",
  },
  {
    key: "pending",
    label: "待处理",
    icon: Clock,
    color: "text-zinc-600 dark:text-zinc-400",
    glow: "from-zinc-500/20",
  },
] as const;

export function SummaryCards({ summary }: SummaryProps) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
      {CARDS.map((c, i) => {
        const Icon = c.icon;
        const v = summary[c.key as keyof typeof summary];
        return (
          <motion.div
            key={c.key}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.04, duration: 0.3 }}
          >
            <Card className="relative overflow-hidden p-4">
              <div
                className={cn(
                  "absolute inset-0 bg-gradient-to-br to-transparent opacity-60 pointer-events-none",
                  c.glow,
                )}
              />
              <div className="relative flex items-start justify-between">
                <div>
                  <div className="text-xs text-muted-foreground">{c.label}</div>
                  <motion.div
                    key={v}
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    className={cn("mt-1 text-3xl font-semibold tabular-nums", c.color)}
                  >
                    {v}
                  </motion.div>
                </div>
                <Icon className={cn("h-4 w-4", c.color)} />
              </div>
            </Card>
          </motion.div>
        );
      })}
    </div>
  );
}
