"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground">
        <main className="max-w-md text-center">
          <p className="text-sm font-semibold text-primary">Something did not finish</p>
          <h1 className="font-display mt-2 text-3xl font-normal">Your work is still safe.</h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            We recorded the failure. Try this screen again, or return to your workspace.
          </p>
          <button
            type="button"
            onClick={reset}
            className="button-primary mt-6"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
