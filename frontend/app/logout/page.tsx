"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { Button } from "../../components/ui/Button";
import { guardedSignOut } from "../../lib/candidateAuthClient";

import { resetAnalyticsIdentity } from "../../lib/analytics";

export default function LogoutPage() {
  const { status: sessionStatus } = useSession();
  const started = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const logout = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      await guardedSignOut();
      resetAnalyticsIdentity();
      window.location.replace(new URL("/login", window.location.origin).href);
    } catch {
      setError("Secure sign-out is pending. Your cookie was kept until your session is denied. Retry to check the same request.");
      setBusy(false);
    }
  }, []);
  useEffect(() => {
    if (sessionStatus === "loading" || started.current) return;
    started.current = true;
    void logout();
  }, [logout, sessionStatus]);


  return (
    <main className="app-page">
      <div className="page-container max-w-xl space-y-5 text-sm text-muted-foreground" aria-busy={busy}>
        <h1 className="font-display text-3xl text-foreground">Sign out securely</h1>
        {busy ? <p role="status">Confirming session denial before signing out…</p> : null}
        {error ? <><p role="alert">{error}</p><Button onClick={() => void logout()}>Retry sign-out</Button>
          <p><Link href="/dashboard" className="text-primary underline">Return to your workspace</Link></p></> : null}
      </div>
    </main>
  );
}
