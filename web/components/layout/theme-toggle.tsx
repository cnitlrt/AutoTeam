"use client";
import { motion } from "framer-motion";
import { Moon, Sun } from "lucide-react";
import { useTheme, type Theme } from "@/components/providers/theme-provider";
import { cn } from "@/lib/utils";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";

  function handle(next: Theme, e: React.MouseEvent) {
    if (next === theme) return;
    setTheme(next, { x: e.clientX, y: e.clientY });
  }

  return (
    <div className="flex items-center justify-between gap-2 px-2 py-1.5">
      <span className="text-sm text-muted-foreground">主题</span>
      <div
        role="radiogroup"
        aria-label="切换主题"
        className="relative flex items-center rounded-full bg-muted/70 p-0.5 border border-border/50"
      >
        <motion.div
          aria-hidden
          className="absolute top-0.5 h-6 w-7 rounded-full bg-card border border-border/60 shadow-sm"
          initial={false}
          animate={{ x: isDark ? 28 : 0 }}
          transition={{ type: "spring", stiffness: 500, damping: 35, mass: 0.6 }}
          style={{ left: 2 }}
        />
        <button
          type="button"
          role="radio"
          aria-checked={!isDark}
          aria-label="浅色模式"
          onClick={(e) => handle("light", e)}
          className={cn(
            "relative z-10 flex h-6 w-7 items-center justify-center rounded-full transition-colors",
            !isDark ? "text-amber-500" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Sun className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={isDark}
          aria-label="深色模式"
          onClick={(e) => handle("dark", e)}
          className={cn(
            "relative z-10 flex h-6 w-7 items-center justify-center rounded-full transition-colors",
            isDark ? "text-sky-300" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Moon className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
