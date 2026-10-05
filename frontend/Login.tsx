import { useState } from "react";
import { useAuth } from "./AuthContext";

// ─── THEME (mirrors App.tsx's palette so this reads as the same product) ─────
const C = {
  bg: "#f5f2ec", panel: "#ede9df", card: "#ffffff", card2: "#f0ece4",
  border: "#d4ccc0", accent: "#d97000", gold: "#b89200", green: "#00936b",
  amber: "#c47c00", red: "#d93030", purple: "#7c3aed", orange: "#c85000",
  text: "#1c1410", muted: "#7a6454", muted2: "#a08870",
};

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    login(username, password).catch((err: any) => setError(err.message || String(err))).finally(() => setSubmitting(false));
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: C.bg, padding: 20 }}>
      <form
        onSubmit={submit}
        style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 14, padding: "32px 28px", width: "min(360px, 100%)", boxShadow: "0 24px 64px rgba(0,0,0,0.12)" }}
      >
        <div style={{ fontSize: 20, fontWeight: 900, color: C.text, marginBottom: 4 }}>ScheduleIQ</div>
        <div style={{ fontSize: 12, color: C.muted, marginBottom: 22 }}>Sign in to continue.</div>

        <div style={{ marginBottom: 14 }}>
          <label htmlFor="login-username" style={{ display: "block", fontSize: 10, color: C.muted, textTransform: "uppercase", marginBottom: 4 }}>Username</label>
          <input
            id="login-username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            style={{ width: "100%", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 7, padding: "9px 11px", fontSize: 14, fontFamily: "inherit", color: C.text, boxSizing: "border-box" }}
          />
        </div>
        <div style={{ marginBottom: 20 }}>
          <label htmlFor="login-password" style={{ display: "block", fontSize: 10, color: C.muted, textTransform: "uppercase", marginBottom: 4 }}>Password</label>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ width: "100%", background: C.card2, border: `1px solid ${C.border}`, borderRadius: 7, padding: "9px 11px", fontSize: 14, fontFamily: "inherit", color: C.text, boxSizing: "border-box" }}
          />
        </div>

        {error && <div style={{ fontSize: 12, color: C.red, background: "#fff0f0", border: `1px solid ${C.red}30`, borderRadius: 7, padding: "8px 10px", marginBottom: 14 }}>⚠ {error}</div>}

        <button
          type="submit"
          disabled={!username || !password || submitting}
          style={{
            width: "100%", padding: "10px 14px", borderRadius: 8, border: "none", cursor: (!username || !password || submitting) ? "default" : "pointer",
            fontFamily: "inherit", fontSize: 14, fontWeight: 700,
            background: (!username || !password || submitting) ? C.card2 : C.accent,
            color: (!username || !password || submitting) ? C.muted : "#fff",
          }}
        >
          {submitting ? "Signing in…" : "Sign In"}
        </button>
      </form>
    </div>
  );
}
