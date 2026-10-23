import * as Sentry from "@sentry/nextjs";
import { resetTelemetry } from "./lib/passwordResetPrivacy";

Sentry.init({
  dsn: process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.APP_ENV || process.env.NODE_ENV,
  tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE || "0.1"),
  sendDefaultPii: false,
  beforeSend: (event) => resetTelemetry(event) ? null : event,
  beforeSendTransaction: (event) => resetTelemetry(event) ? null : event,
  beforeBreadcrumb: (breadcrumb) => resetTelemetry(breadcrumb) ? null : breadcrumb,
  enabled: Boolean(process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN),
});
