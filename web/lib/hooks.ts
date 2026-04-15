"use client";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = [],
): { data: T | null; error: Error | null; loading: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    const run = async () => {
      try {
        const d = await fetcher();
        if (!cancelled && mounted.current) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (!cancelled && mounted.current) {
          setError(e as Error);
        }
      } finally {
        if (!cancelled && mounted.current) {
          setLoading(false);
          if (intervalMs > 0) timer = setTimeout(run, intervalMs);
        }
      }
    };
    run();
    return () => {
      cancelled = true;
      mounted.current = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  return { data, error, loading, refresh: () => setTick((t) => t + 1) };
}

export function isUnauthorized(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}
