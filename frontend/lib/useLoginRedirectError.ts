"use client";

import { useSyncExternalStore } from "react";

const subscribe = (onChange: () => void) => {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
};
const clientSnapshot = () => window.location.search;
const serverSnapshot = () => "";

export function loginRedirectError(search: string): string | null {
  const params = new URLSearchParams(search);
  const code = params.get("error");
  if (!code) {
    return params.get("csrf") === "true"
      ? "Your sign-in request expired. Please start sign-in again."
      : null;
  }

  switch (code) {
    case "Callback":
    case "OAuthCreateAccount":
    case "AUTH_UNAVAILABLE":
    case "Configuration":
      return "Sign-in is temporarily unavailable. Please try again later.";
    case "OAuthCallback":
      return "Google sign-in could not be verified. Please start sign-in again.";
    case "OAuthSignin":
      return "Could not start Google sign-in. Please try again.";
    case "OAuthAccountNotLinked":
      return "Sign in with the method you originally used for this account.";
    case "AccessDenied":
      return "Sign-in was not allowed. Please try again or use another sign-in method.";
    case "CredentialsSignin":
      return "Could not sign in. Check your email and password.";
    case "SessionRequired":
      return "Please sign in to continue.";
    case "SESSION_LOGOUT_REQUIRED":
      return "Sign out of the current account before switching accounts.";
    case "AUTH_REQUEST_REJECTED":
      return "Your sign-in request could not be verified. Please start sign-in again.";
    default:
      return "Sign-in could not be completed. Please try again.";
  }
}

export function useLoginRedirectError(): string | null {
  return loginRedirectError(useSyncExternalStore(subscribe, clientSnapshot, serverSnapshot));
}
