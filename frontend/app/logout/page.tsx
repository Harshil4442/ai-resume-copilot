"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { signOut } from "next-auth/react";

export default function LogoutPage() {
  const router = useRouter();
  useEffect(() => {
    signOut({ redirect: false }).then(() => {
      localStorage.removeItem("access_token");
      router.push("/login");
    });
    // `signOut` is a stable module-level import.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);


  return (
    <main className="max-w-md mx-auto py-10">
      <div className="text-sm">Signing out…</div>
    </main>
  );
}
