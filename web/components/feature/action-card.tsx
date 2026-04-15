"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { Loader2, type LucideIcon } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface ActionCardProps {
  icon: LucideIcon;
  title: string;
  description: string;
  accent: string;
  buttonLabel: string;
  disabled?: boolean;
  disabledHint?: string;
  paramLabel?: string;
  paramPlaceholder?: string;
  defaultParam?: string;
  onRun: (value: string) => Promise<void>;
}

export function ActionCard({
  icon: Icon,
  title,
  description,
  accent,
  buttonLabel,
  disabled,
  disabledHint,
  paramLabel,
  paramPlaceholder,
  defaultParam = "",
  onRun,
}: ActionCardProps) {
  const [value, setValue] = useState(defaultParam);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    try {
      await onRun(value);
    } finally {
      setLoading(false);
    }
  }

  return (
    <motion.div
      whileHover={{ y: -2 }}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
    >
      <Card className="relative overflow-hidden h-full">
        <div
          className={cn(
            "absolute inset-x-0 top-0 h-20 pointer-events-none opacity-70 bg-gradient-to-b to-transparent",
            accent,
          )}
        />
        <div className="relative p-5 flex flex-col h-full">
          <div className="flex items-start gap-3 mb-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-background/40 border border-border/60">
              <Icon className="h-4 w-4" />
            </div>
            <div>
              <div className="font-medium">{title}</div>
              <div className="text-xs text-muted-foreground mt-0.5">{description}</div>
            </div>
          </div>
          {paramLabel && (
            <div className="mb-3 space-y-1.5">
              <div className="text-xs text-muted-foreground">{paramLabel}</div>
              <Input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={paramPlaceholder}
                disabled={disabled || loading}
                type="number"
                min={0}
              />
            </div>
          )}
          <div className="mt-auto space-y-2">
            {disabled && disabledHint && (
              <div className="text-[11px] text-amber-400/90">{disabledHint}</div>
            )}
            <Button onClick={run} disabled={disabled || loading} className="w-full">
              {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {loading ? "执行中..." : buttonLabel}
            </Button>
          </div>
        </div>
      </Card>
    </motion.div>
  );
}
