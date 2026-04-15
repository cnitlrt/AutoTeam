"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { LogOut, Sparkles } from "lucide-react";
import { NAV_ITEMS } from "./nav-items";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/providers/auth-provider";
import { ThemeToggle } from "./theme-toggle";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const pathname = usePathname();
  const { logout, authRequired } = useAuth();
  return (
    <aside className="glass hidden md:flex h-screen sticky top-0 w-64 shrink-0 flex-col border-r border-border/50">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500/30 to-indigo-500/30 border border-sky-500/20">
          <Sparkles className="h-4 w-4 text-sky-300" />
        </div>
        <div>
          <div className="text-sm font-semibold tracking-tight">AutoTeam</div>
          <div className="text-[10px] text-muted-foreground uppercase tracking-widest">Control Panel</div>
        </div>
      </div>
      <nav className="flex-1 px-3 space-y-1 overflow-y-auto scrollbar-thin">
        {NAV_ITEMS.map((item) => {
          const active = pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link key={item.key} href={item.href} className="block">
              <motion.div
                whileHover={{ x: 2 }}
                className={cn(
                  "relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-accent/70 text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/40",
                )}
              >
                {active && (
                  <motion.div
                    layoutId="sidebar-active"
                    className="absolute inset-0 rounded-lg border border-border/60 bg-gradient-to-r from-accent/60 to-transparent"
                    transition={{ type: "spring", stiffness: 380, damping: 30 }}
                  />
                )}
                <span className="relative flex items-center gap-3">
                  <Icon className="h-4 w-4" />
                  <span className="font-medium">{item.label}</span>
                </span>
              </motion.div>
            </Link>
          );
        })}
      </nav>
      <div className="p-3 border-t border-border/50 space-y-1">
        <ThemeToggle />
        {authRequired ? (
          <>
            <Separator className="my-1" />
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start gap-2 text-muted-foreground"
              onClick={logout}
            >
              <LogOut className="h-4 w-4" />
              退出登录
            </Button>
          </>
        ) : (
          <div className="text-[10px] text-muted-foreground px-2 pt-1">未启用 API Key 鉴权</div>
        )}
      </div>
    </aside>
  );
}

export function MobileNav() {
  const pathname = usePathname();
  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 glass border-t border-border/60 pb-[env(safe-area-inset-bottom)]">
      <div className="flex items-stretch justify-around">
        {NAV_ITEMS.slice(0, 5).map((item) => {
          const active = pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.key}
              href={item.href}
              className={cn(
                "flex-1 flex flex-col items-center gap-0.5 py-2 text-[10px] transition-colors",
                active ? "text-foreground" : "text-muted-foreground",
              )}
            >
              <Icon className={cn("h-5 w-5", active && "text-sky-400")} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
