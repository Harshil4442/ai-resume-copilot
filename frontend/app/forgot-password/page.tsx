import type { Metadata } from "next";
import PasswordResetRequest from "../../components/PasswordResetRequest";

export const metadata: Metadata = { title: "Forgot password", referrer: "no-referrer", robots: { index: false, follow: false } };
export default function ForgotPasswordPage() {
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-md"><PasswordResetRequest /></div></main>;
}
