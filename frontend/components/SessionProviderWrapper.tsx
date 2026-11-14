"use client";

import React from "react";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { SessionProvider, useSession } from "next-auth/react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { capturePasswordResetLink } from "../lib/passwordResetLink";
import { capturePrivacyErasureLink } from "../lib/privacyErasureLink";
import { ownedBootstrapKey } from "../lib/ownedBootstrapClient";
import { ownedDashboardKey } from "../lib/ownedDashboardClient";
import { retryRead } from "../lib/readQueryPolicy";

// Keep these destinations aligned with the authentication matcher in proxy.ts.
const PRIVATE_ROUTES = ["/dashboard", "/resume", "/jobs", "/employer-jobs", "/workspace", "/market", "/learning", "/profile", "/billing"];

function OwnerQueryProvider({
  children,
  privatePage,
  reauthenticationRequired,
}: {
  children: React.ReactNode;
  privatePage: boolean;
  reauthenticationRequired: boolean;
}) {
  const [accountDenied, setAccountDenied] = useState(false);
  const [queryClient] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({
          onError(error, query) {
            const key = query.queryKey;
            if (!error || typeof error !== "object" || !("status" in error) || error.status !== 401) return;
            const isOwnedSnapshot = [ownedBootstrapKey, ownedDashboardKey].some(
              (ownedKey) => key.length === ownedKey.length && ownedKey.every((part, index) => key[index] === part),
            );
            if (isOwnedSnapshot) setAccountDenied(true);
          },
        }),
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: retryRead,
            refetchOnWindowFocus: false,
          },
          mutations: { retry: 0 },
        },
      }),
  );

  if (privatePage && (reauthenticationRequired || accountDenied)) {
    return <main className="app-page"><div className="page-container space-y-3">
      <h1 className="font-display text-3xl">Sign in again</h1>
      <p role="status">Your HireWiz session has ended. Sign out securely, then sign in again to open your workspace.</p>
      <a className="text-primary underline" href="/logout">Sign out securely</a>
    </div></main>;
  }

  return <QueryClientProvider client={queryClient}><Tooltip.Provider delayDuration={350}>{children}</Tooltip.Provider></QueryClientProvider>;
}

function SessionOwnerBoundary({ children }: { children: React.ReactNode }) {
  const { data: session, status } = useSession();
  const pathname = usePathname();
  // A route exit clears proof; a same-document owner-boundary remount does not.
  useEffect(() => { capturePasswordResetLink(); capturePrivacyErasureLink(); }, [pathname]);
  const privatePage = PRIVATE_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));
  const identity = session?.user?.id || session?.user?.email;
  const owner = status === "authenticated"
    ? `owner:${identity}`
    : status;

  if (status === "authenticated" && !identity) {
    return <p role="alert">Your session could not be identified. <a href="/logout">Log in again</a> to continue.</p>;
  }
  if (privatePage && status !== "authenticated") {
    return <main className="app-page"><div className="page-container text-sm text-muted-foreground" role="status">{status === "loading" ? "Loading your workspace…" : <a href="/login" className="underline">Sign in to continue to your workspace.</a>}</div></main>;
  }

  // Remount both queries and page-local drafts before another owner's children render.
  // Loading and signed-out sessions also have separate caches from authenticated users.
  return <OwnerQueryProvider key={owner} privatePage={privatePage || pathname === "/browser-companion"}
    reauthenticationRequired={session?.reauthenticationRequired === true}>{children}</OwnerQueryProvider>;
}

export default function SessionProviderWrapper({ children }: { children: React.ReactNode }) {
  return <SessionProvider><SessionOwnerBoundary>{children}</SessionOwnerBoundary></SessionProvider>;
}
