import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { SESSION_EXPIRED_EVENT } from "./auth";

// Phase 3 (Authentication and Authorization) — the single source of truth
// for "who is logged in" across the whole app. Calls /api/auth/me/ once on
// mount (which also refreshes the CSRF cookie — see auth_views.me_view);
// App.tsx renders <Login/> instead of the app itself until `user` is set.

export type AuthUser = { id: number; username: string; email: string; role: "ADMINISTRATOR" | "SCHEDULER" | "VIEWER"; isSuperuser: boolean };

type AuthState = {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    return fetch("/api/auth/me/")
      .then((r) => r.json())
      .then((d) => setUser(d.authenticated ? d.user : null))
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    const resp = await fetch("/api/auth/login/", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const text = await resp.text();
    if (!resp.ok) {
      let msg = text;
      try { msg = JSON.parse(text).error || text; } catch {}
      setError(msg);
      throw new Error(msg);
    }
    const data = JSON.parse(text);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    await fetch("/api/auth/logout/", { method: "POST" }).catch(() => {});
    setUser(null);
  }, []);

  return <AuthContext.Provider value={{ user, loading, error, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

const ROLE_RANK: Record<string, number> = { VIEWER: 0, SCHEDULER: 1, ADMINISTRATOR: 2 };
/** Mirrors scheduler/permissions.py's _meets() — UI-only convenience for
 * hiding controls a user can't use; the server enforces the real
 * boundary regardless of what this returns, same principle as every
 * confirmation checkbox elsewhere in this app. */
export function hasAtLeastRole(user: AuthUser | null, required: string): boolean {
  if (!user) return false;
  return (ROLE_RANK[user.role] ?? -1) >= (ROLE_RANK[required] ?? 999);
}
