"use client";

import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { SessionProvider } from "next-auth/react";
import { useState } from "react";

export default function SessionProviderWrapper({
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

  return (
    <SessionProvider>
      <QueryClientProvider client={queryClient}>
        <Tooltip.Provider delayDuration={350}>{children}</Tooltip.Provider>
      </QueryClientProvider>
    </SessionProvider>
  );
}
