"use client";
import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Monitor, MonitorOff } from "lucide-react";
import { getApiKey } from "@/lib/api";
import { cn } from "@/lib/utils";

interface VncViewerProps {
  active: boolean;
  label?: string;
  elapsed?: number;
  className?: string;
}

export function VncViewer({ active, label, elapsed, className }: VncViewerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<{ disconnect: () => void } | null>(null);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active || !hostRef.current) return;
    let cancelled = false;
    setConnecting(true);
    setError(null);

    (async () => {
      try {
        const mod = await import("@novnc/novnc/lib/rfb.js");
        if (cancelled || !hostRef.current) return;
        const RFB = mod.default;
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const key = getApiKey();
        const url = `${proto}//${window.location.host}/api/vnc/ws${
          key ? `?key=${encodeURIComponent(key)}` : ""
        }`;
        const rfb = new RFB(hostRef.current, url, { credentials: { password: "" } });
        rfb.viewOnly = true;
        rfb.scaleViewport = true;
        rfb.resizeSession = false;
        rfb.showDotCursor = false;
        rfb.background = "transparent";
        rfb.addEventListener("connect", () => {
          if (!cancelled) {
            setConnecting(false);
            setConnected(true);
          }
        });
        rfb.addEventListener("disconnect", (e: Event) => {
          if (cancelled) return;
          setConnected(false);
          setConnecting(false);
          const detail = (e as CustomEvent).detail as { clean?: boolean } | undefined;
          if (detail && detail.clean === false) {
            setError("连接中断");
          }
        });
        rfbRef.current = rfb;
      } catch (e) {
        if (!cancelled) {
          setConnecting(false);
          setError((e as Error).message);
        }
      }
    })();

    return () => {
      cancelled = true;
      try {
        rfbRef.current?.disconnect();
      } catch {
        /* ignore */
      }
      rfbRef.current = null;
      setConnected(false);
      setConnecting(false);
      setError(null);
    };
  }, [active]);

  return (
    <div
      className={cn(
        "relative rounded-lg border border-border/60 overflow-hidden bg-black/80",
        className,
      )}
    >
      <div className="absolute top-2 left-2 z-10 flex items-center gap-2 rounded-full bg-black/60 backdrop-blur px-2.5 py-1 text-[11px] text-white/80 border border-white/10">
        {active ? (
          <>
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
            </span>
            <span className="font-medium">{label || "browser"}</span>
            {typeof elapsed === "number" && <span className="text-white/50">{elapsed}s</span>}
          </>
        ) : (
          <>
            <MonitorOff className="h-3 w-3" />
            <span>空闲</span>
          </>
        )}
      </div>

      <div ref={hostRef} className={cn("h-full w-full", active ? "" : "hidden")} />

      {!active && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center justify-center gap-3 h-full py-20 text-muted-foreground"
        >
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted/30 border border-border/60">
            <Monitor className="h-5 w-5" />
          </div>
          <div className="text-sm">浏览器未运行</div>
          <div className="text-xs opacity-70">任务触发时会在此实时显示</div>
        </motion.div>
      )}
      {active && connecting && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-white/60">
          连接 VNC 中…
        </div>
      )}
      {active && error && (
        <div className="absolute inset-x-0 bottom-2 mx-auto w-max rounded-full bg-rose-500/20 border border-rose-500/40 px-3 py-1 text-[11px] text-rose-200">
          {error}
        </div>
      )}
      {!connected && active && !error && !connecting && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-white/60">
          等待数据…
        </div>
      )}
    </div>
  );
}
