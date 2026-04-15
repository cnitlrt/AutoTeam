"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { api, clearApiKey, getApiKey, setApiKey } from "@/lib/api";

type AuthState = "loading" | "unauthenticated" | "authenticated" | "needs-setup";

interface AuthContextValue {
  state: AuthState;
  authRequired: boolean;
  configured: boolean;
  login: (key: string) => Promise<boolean>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const PUBLIC_PATHS = new Set(["/login", "/login/", "/setup", "/setup/"]);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>("loading");
  const [authRequired, setAuthRequired] = useState(false);
  const [configured, setConfigured] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  const refresh = useCallback(async () => {
    try {
      const setup = await api.getSetupStatus();
      setConfigured(setup.configured);
      if (!setup.configured) {
        setState("needs-setup");
        return;
      }
      const auth = await api.checkAuth();
      setAuthRequired(auth.auth_required);
      if (!auth.auth_required || auth.authenticated) {
        setState("authenticated");
      } else {
        setState("unauthenticated");
      }
    } catch (e) {
      const err = e as { status?: number };
      if (err.status === 401) {
        setAuthRequired(true);
        setState("unauthenticated");
      } else {
        setState("unauthenticated");
      }
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (state === "loading") return;
    const isPublic = PUBLIC_PATHS.has(pathname);
    if (state === "needs-setup" && !pathname.startsWith("/setup")) {
      router.replace("/setup");
    } else if (state === "unauthenticated" && !isPublic) {
      router.replace("/login");
    } else if (state === "authenticated" && (pathname === "/" || pathname === "/login" || pathname === "/login/" || pathname === "/setup" || pathname === "/setup/")) {
      router.replace("/dashboard");
    }
  }, [state, pathname, router]);

  const login = useCallback(
    async (key: string) => {
      setApiKey(key);
      try {
        const auth = await api.checkAuth();
        if (auth.authenticated || !auth.auth_required) {
          setAuthRequired(auth.auth_required);
          setState("authenticated");
          return true;
        }
        clearApiKey();
        return false;
      } catch {
        clearApiKey();
        return false;
      }
    },
    [],
  );

  const logout = useCallback(() => {
    clearApiKey();
    setState("unauthenticated");
    router.replace("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ state, authRequired, configured, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export { getApiKey, setApiKey, clearApiKey };
