import { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import GoogleProvider from "next-auth/providers/google";

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID || "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET || "",
    }),
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
          
          const user = await res.json();
          if (user && user.access_token) {
            return {
              id: credentials.email,
              email: credentials.email,
              accessToken: user.access_token,
            } as any;
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
        let backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
        backendUrl = backendUrl.replace(/\/+$/, "");
        try {
          const res = await fetch(`${backendUrl}/api/auth/google-login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              email: user.email,
              name: user.name || "",
            }),
          });
          if (res.ok) {
            const data = await res.json();
            if (data.access_token) {
              token.accessToken = data.access_token;
            }
          }
        } catch (e) {
          console.error("Google backend login error:", e);
        }
      } else if (user && (user as any).accessToken) {
        token.accessToken = (user as any).accessToken;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        (session.user as any).accessToken = token.accessToken;
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
  secret: process.env.NEXTAUTH_SECRET || "default-nextauth-secret-needs-change-32-chars",
};
