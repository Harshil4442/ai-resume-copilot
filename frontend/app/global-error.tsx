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
      <body className="flex min-h-screen items-center justify-center bg-neutral-950 px-6 text-neutral-50">
        <main className="max-w-md text-center">
          <p className="text-sm font-semibold text-teal-300">Something did not finish</p>
          <h1 className="mt-2 text-3xl font-bold">Your work is still safe.</h1>
          <p className="mt-3 text-sm leading-6 text-neutral-400">
            We recorded the failure. Try this screen again, or return to your workspace.
          </p>
          <button
            type="button"
            onClick={reset}
            className="mt-6 rounded-md bg-teal-400 px-4 py-2.5 text-sm font-bold text-neutral-950 hover:bg-teal-300"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
