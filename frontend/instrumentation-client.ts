import * as Sentry from "@sentry/nextjs";
import { capturePasswordResetLink } from "./lib/passwordResetLink";
import { capturePrivacyErasureLink } from "./lib/privacyErasureLink";
import { isPasswordResetPath, isPasswordResetUrl, resetTelemetry, setResetGoogleExclusion } from "./lib/passwordResetPrivacy";

capturePasswordResetLink();
capturePrivacyErasureLink();
setResetGoogleExclusion(isPasswordResetPath(window.location.pathname));

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NEXT_PUBLIC_APP_ENV || process.env.NODE_ENV,
  tracesSampleRate: Number(process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE || "0.1"),
  sendDefaultPii: false,
  enabled: Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN) && !isPasswordResetPath(window.location.pathname),
  beforeSend: (event) => resetTelemetry(event) ? null : event,
  beforeSendTransaction: (event) => resetTelemetry(event) ? null : event,
  beforeBreadcrumb: (breadcrumb) => resetTelemetry(breadcrumb) ? null : breadcrumb,
});

export const onRouterTransitionStart: typeof Sentry.captureRouterTransitionStart = (...args) => {
  if (isPasswordResetPath(window.location.pathname) || isPasswordResetUrl(args[0])) {
    setResetGoogleExclusion(true);
    return;
  }
  return Sentry.captureRouterTransitionStart(...args);
};
