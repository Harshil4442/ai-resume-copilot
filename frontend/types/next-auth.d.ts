import type { DefaultSession, DefaultUser } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
    } & DefaultSession["user"];
  }

  interface User extends DefaultUser {
    /** Private authorize-to-JWT handoff; never copied into public Session. */
    browserPairingSession?: unknown;
    accessToken?: string;
    hirewizUserId?: number;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    /** Installed only by future retained native session provisioning; never exposed by session(). */
    browserPairingSession?: unknown;
    accessToken?: string;
    hirewizUserId?: number;
  }
}
