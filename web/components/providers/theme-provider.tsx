"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "autoteam_theme";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme, origin?: { x: number; y: number }) => void;
  toggle: (origin?: { x: number; y: number }) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function applyTheme(t: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", t === "dark");
  root.style.colorScheme = t;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("dark");

  useEffect(() => {
    const stored = (typeof window !== "undefined"
      ? (window.localStorage.getItem(STORAGE_KEY) as Theme | null)
      : null);
    const initial: Theme =
      stored ??
      (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark");
    setThemeState(initial);
    applyTheme(initial);
  }, []);

  const setTheme = useCallback((next: Theme, origin?: { x: number; y: number }) => {
    const run = () => {
      window.localStorage.setItem(STORAGE_KEY, next);
      applyTheme(next);
      setThemeState(next);
    };
    const doc = document as Document & {
      startViewTransition?: (cb: () => void) => { ready: Promise<void> };
    };
    if (!doc.startViewTransition) {
      run();
      return;
    }
    const root = document.documentElement;
    if (origin) {
      root.style.setProperty("--vt-x", `${origin.x}px`);
      root.style.setProperty("--vt-y", `${origin.y}px`);
      const r = Math.hypot(
        Math.max(origin.x, window.innerWidth - origin.x),
        Math.max(origin.y, window.innerHeight - origin.y),
      );
      root.style.setProperty("--vt-r", `${r}px`);
    } else {
      root.style.setProperty("--vt-x", "50%");
      root.style.setProperty("--vt-y", "50%");
      root.style.setProperty("--vt-r", "150vmax");
    }
    root.setAttribute("data-vt-direction", next === "dark" ? "to-dark" : "to-light");
    doc.startViewTransition(run);
  }, []);

  const toggle = useCallback(
    (origin?: { x: number; y: number }) => {
      setTheme(theme === "dark" ? "light" : "dark", origin);
    },
    [theme, setTheme],
  );

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggle }}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
