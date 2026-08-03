import type { DefaultSession, DefaultUser } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
    } & DefaultSession["user"];
  }

  interface User extends DefaultUser {
    accessToken?: string;
    hirewizUserId?: number;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    hirewizUserId?: number;
  }
}
