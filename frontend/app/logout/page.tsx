"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { signOut } from "next-auth/react";

import { resetAnalyticsIdentity } from "../../lib/analytics";

export default function LogoutPage() {
  const router = useRouter();
  useEffect(() => {
    resetAnalyticsIdentity();
    signOut({ redirect: false }).then(() => {
      router.push("/login");
    });
  }, [router]);


  return (
    <main className="app-page">
      <div className="page-container text-sm text-muted-foreground" role="status">Signing out…</div>
    </main>
  );
}
