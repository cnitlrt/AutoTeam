"use client";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { NAV_ITEMS } from "./nav-items";
import { Badge } from "@/components/ui/badge";

export function Topbar({ authRequired }: { authRequired: boolean }) {
  const pathname = usePathname();
  const item =
    NAV_ITEMS.find((n) => pathname.startsWith(n.href)) ?? NAV_ITEMS[0];
  return (
    <div className="flex items-start justify-between mb-6">
      <motion.div
        key={item.key}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
      >
        <h1 className="text-2xl font-semibold tracking-tight">{item.label}</h1>
        <p className="text-sm text-muted-foreground mt-1">{item.description}</p>
      </motion.div>
      <div className="flex items-center gap-2">
        {!authRequired && <Badge variant="warning">API Key 未启用</Badge>}
      </div>
    </div>
  );
}
