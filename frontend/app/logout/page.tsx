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
    <main className="max-w-md mx-auto py-10">
      <div className="text-sm">Signing out…</div>
    </main>
  );
}
