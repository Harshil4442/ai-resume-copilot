import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Create Account",
  description: "Create a HireWiz account and acknowledge the Terms of Service and Privacy Policy.",
  alternates: { canonical: "/register" },
};

export default function RegisterLayout({ children }: { children: React.ReactNode }) {
  return children;
}
