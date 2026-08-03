import { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import GoogleProvider from "next-auth/providers/google";
import { cookies } from "next/headers";

const POLICY_VERSION = "2026-07-11";
const GOOGLE_CONSENT_COOKIE = "hirewiz_google_registration_consent";

type BackendAuthToken = {
  access_token?: unknown;
  user_id?: unknown;
};

const configuredNextAuthSecret = process.env.NEXTAUTH_SECRET?.trim();
if (
  process.env.NODE_ENV === "production" &&
  (!configuredNextAuthSecret || configuredNextAuthSecret.length < 32)
) {
  throw new Error("NEXTAUTH_SECRET must be configured with at least 32 characters in production.");
}

const nextAuthSecret =
  configuredNextAuthSecret || "hirewiz-local-development-secret-change-me";
const googleClientId = process.env.GOOGLE_CLIENT_ID?.trim() || "";
const googleClientSecret = process.env.GOOGLE_CLIENT_SECRET?.trim() || "";
const googleProvider =
  googleClientId && googleClientSecret
    ? GoogleProvider({ clientId: googleClientId, clientSecret: googleClientSecret })
    : null;

export const authOptions: NextAuthOptions = {
  providers: [
    ...(googleProvider ? [googleProvider] : []),
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        email: { label: "Email", type: "text" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) return null;
        
        let backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
        backendUrl = backendUrl.replace(/\/+$/, "");
        
        try {
          const res = await fetch(`${backendUrl}/api/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              email: credentials.email,
              password: credentials.password,
            }),
          });
          
          if (!res.ok) return null;
          
          const user = (await res.json()) as BackendAuthToken;
          if (typeof user.access_token === "string" && typeof user.user_id === "number") {
            return {
              id: String(user.user_id),
              email: credentials.email,
              accessToken: user.access_token,
              hirewizUserId: user.user_id,
            };
          }
          return null;
        } catch (e) {
          console.error("Authorize error:", e);
          return null;
        }
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user, account }) {
      if (account?.provider === "google" && user?.email) {
        if (!account.id_token) {
          throw new Error("Google did not return a signed identity token.");
        }
        let backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
        backendUrl = backendUrl.replace(/\/+$/, "");
        let registrationConsent = false;
        try {
          registrationConsent = (await cookies()).get(GOOGLE_CONSENT_COOKIE)?.value === POLICY_VERSION;
        } catch {
          // No request cookie context means a new account must fail closed.
        }
        try {
          const res = await fetch(`${backendUrl}/api/auth/google-login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id_token: account.id_token,
              registration_consent: registrationConsent,
              policy_version: registrationConsent ? POLICY_VERSION : null,
            }),
          });
          if (!res.ok) {
            throw new Error(`Backend rejected Google sign-in (${res.status}).`);
          }
          const data = (await res.json()) as BackendAuthToken;
          if (typeof data.access_token !== "string" || typeof data.user_id !== "number") {
            throw new Error("Backend did not return an access token.");
          }
          token.accessToken = data.access_token;
          token.hirewizUserId = data.user_id;
        } catch (e) {
          console.error("Google backend login error:", e);
          throw e;
        }
      } else if (user?.accessToken && user.hirewizUserId) {
        token.accessToken = user.accessToken;
        token.hirewizUserId = user.hirewizUserId;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user && token.hirewizUserId) {
        session.user.id = String(token.hirewizUserId);
      }
      return session;
    },
  },
  pages: {
    signIn: "/login",
    signOut: "/logout",
  },
  session: {
    strategy: "jwt",
  },
  secret: nextAuthSecret,
};
