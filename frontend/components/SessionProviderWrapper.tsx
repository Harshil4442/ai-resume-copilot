"use client";

import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { SessionProvider, useSession } from "next-auth/react";
import { usePathname } from "next/navigation";
import { useState } from "react";

// Keep these destinations aligned with the authentication matcher in proxy.ts.
const PRIVATE_ROUTES = ["/dashboard", "/resume", "/jobs", "/employer-jobs", "/workspace", "/market", "/learning", "/profile", "/billing"];

function OwnerQueryProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
          mutations: { retry: 0 },
        },
      }),
  );

  return <QueryClientProvider client={queryClient}><Tooltip.Provider delayDuration={350}>{children}</Tooltip.Provider></QueryClientProvider>;
}

function SessionOwnerBoundary({ children }: { children: React.ReactNode }) {
  const { data: session, status } = useSession();
  const pathname = usePathname();
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
  return <OwnerQueryProvider key={owner}>{children}</OwnerQueryProvider>;
}

export default function SessionProviderWrapper({ children }: { children: React.ReactNode }) {
  return <SessionProvider><SessionOwnerBoundary>{children}</SessionOwnerBoundary></SessionProvider>;
}
