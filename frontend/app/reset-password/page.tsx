import type { Metadata } from "next";
import PasswordResetConfirm from "../../components/PasswordResetConfirm";

export const metadata: Metadata = { title: "Reset password", referrer: "no-referrer", robots: { index: false, follow: false } };
export default function ResetPasswordPage() {
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-md"><PasswordResetConfirm /></div></main>;
}
