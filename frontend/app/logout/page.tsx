"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { logout } from "../../lib/auth";

export default function LogoutPage(): JSX.Element {
  const router = useRouter();
  useEffect(() => {
    logout();
    router.push("/login");
    // `logout` is a stable module-level import.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  return (
    <main className="max-w-md mx-auto py-10">
      <div className="text-sm">Signing out…</div>
    </main>
  );
}
