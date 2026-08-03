"use client";

import Script from "next/script";
import { useSession } from "next-auth/react";
import { usePathname } from "next/navigation";
import posthog from "posthog-js";
import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "hirewiz_cookie_consent";
const CONSENT_VERSION = 2;
const CONSENT_MAX_AGE_MS = 365 * 24 * 60 * 60 * 1000;

type Preference = "analytics" | "essential";
type StoredConsent = {
  version: number;
  preference: Preference;
  savedAt: string;
};

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

function readStoredConsent(): Preference | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredConsent>;
    const savedAt = parsed.savedAt ? new Date(parsed.savedAt).getTime() : Number.NaN;
    if (
      parsed.version !== CONSENT_VERSION ||
      (parsed.preference !== "analytics" && parsed.preference !== "essential") ||
      !Number.isFinite(savedAt) ||
      Date.now() - savedAt > CONSENT_MAX_AGE_MS
    ) {
      window.localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed.preference;
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function removeAnalyticsStorage() {
  const cookieNames = document.cookie
    .split(";")
    .map((cookie) => cookie.split("=")[0]?.trim())
    .filter((name): name is string => Boolean(name && (name === "_ga" || name.startsWith("_ga_") || name.startsWith("ph_"))));

  for (const name of cookieNames) {
    document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`;
    document.cookie = `${name}=; Max-Age=0; Path=/; Domain=.hirewizhq.com; SameSite=Lax`;
    document.cookie = `${name}=; Max-Age=0; Path=/; Domain=www.hirewizhq.com; SameSite=Lax`;
  }
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const name = window.localStorage.key(index);
    if (name?.startsWith("ph_")) window.localStorage.removeItem(name);
  }
}

export default function AnalyticsConsent({
  gaMeasurementId,
  posthogKey,
  posthogHost,
}: {
  gaMeasurementId: string | null;
  posthogKey: string | null;
  posthogHost: string;
}) {
  const [preference, setPreference] = useState<Preference | null>(null);
  const [ready, setReady] = useState(false);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const pathname = usePathname();
  const { data: session, status: sessionStatus } = useSession();
  const identifiedUser = useRef<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPreference(readStoredConsent());
      setReady(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (preference !== "analytics" || !posthogKey) {
      try {
        posthog.opt_out_capturing();
      } catch {
        // The SDK may not have been initialized yet.
      }
      return;
    }
    posthog.init(posthogKey, {
      api_host: posthogHost,
      autocapture: false,
      capture_pageview: false,
      capture_pageleave: true,
      person_profiles: "identified_only",
      persistence: "localStorage+cookie",
      respect_dnt: true,
      opt_out_capturing_by_default: false,
    });
    posthog.opt_in_capturing();
  }, [posthogHost, posthogKey, preference]);

  useEffect(() => {
    if (preference === "analytics" && posthogKey) {
      posthog.capture("$pageview", { $current_url: window.location.href, path: pathname });
    }
  }, [pathname, posthogKey, preference]);

  useEffect(() => {
    if (preference !== "analytics" || !posthogKey) {
      identifiedUser.current = null;
      return;
    }
    const userId = session?.user?.id;
    if (sessionStatus === "authenticated" && userId && identifiedUser.current !== userId) {
      posthog.identify(userId);
      identifiedUser.current = userId;
    }
    if (sessionStatus === "unauthenticated" && identifiedUser.current) {
      posthog.reset();
      identifiedUser.current = null;
    }
  }, [posthogKey, preference, session?.user?.id, sessionStatus]);

  useEffect(() => {
    const open = () => setPreferencesOpen(true);
    window.addEventListener("hirewiz:open-cookie-preferences", open);
    return () => window.removeEventListener("hirewiz:open-cookie-preferences", open);
  }, []);

  const savePreference = useCallback((next: Preference) => {
    const record: StoredConsent = {
      version: CONSENT_VERSION,
      preference: next,
      savedAt: new Date().toISOString(),
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
    setPreference(next);
    setPreferencesOpen(false);

    if (next === "essential") {
      window.gtag?.("consent", "update", { analytics_storage: "denied" });
      try {
        posthog.reset();
      } catch {
        // The SDK may not have been initialized yet.
      }
      removeAnalyticsStorage();
      posthog.opt_out_capturing();
    }
  }, []);

  const analyticsAllowed = Boolean(gaMeasurementId && preference === "analytics");
  const analyticsConfigured = Boolean(gaMeasurementId || posthogKey);
  const showPrompt = ready && (Boolean(analyticsConfigured && preference === null) || preferencesOpen);

  return (
    <>
      {analyticsAllowed ? (
        <>
          <Script
            src={`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(gaMeasurementId!)}`}
            strategy="afterInteractive"
          />
          <Script id="hirewiz-ga-init" strategy="afterInteractive">
            {`
              window.dataLayer = window.dataLayer || [];
              function gtag(){dataLayer.push(arguments);}
              window.gtag = gtag;
              gtag('consent', 'default', { analytics_storage: 'granted' });
              gtag('js', new Date());
              gtag('config', '${gaMeasurementId}', { allow_google_signals: false });
            `}
          </Script>
        </>
      ) : null}

      {showPrompt ? (
        <div className="fixed inset-x-0 bottom-0 z-[100] px-4 pb-4 sm:px-6" role="dialog" aria-label="Cookie preferences">
          <div className="mx-auto max-w-4xl rounded-2xl border border-slate-700 bg-slate-950/95 p-5 shadow-2xl backdrop-blur-xl sm:flex sm:items-center sm:justify-between sm:gap-6">
            <div>
              <h2 className="text-sm font-black text-white">Choose your cookie preference</h2>
              <p className="mt-1 text-xs leading-relaxed text-slate-400">
                {analyticsConfigured
                  ? "Essential storage keeps sign-in and your preference working. With your permission, analytics cookies help us understand aggregate site use. Advertising cookies are not used."
                  : "Analytics is not currently configured. Only essential authentication, security, and preference storage is available."}
              </p>
            </div>
            <div className="mt-4 flex flex-col gap-2 sm:mt-0 sm:min-w-[280px] sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={() => savePreference("essential")}
                className="rounded-xl border border-slate-600 px-4 py-2.5 text-xs font-bold text-slate-200 hover:bg-slate-800"
              >
                Essential only
              </button>
              {analyticsConfigured ? (
                <button
                  type="button"
                  onClick={() => savePreference("analytics")}
                  className="rounded-xl bg-primary px-4 py-2.5 text-xs font-bold text-white hover:bg-primary/90"
                >
                  Accept analytics
                </button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
