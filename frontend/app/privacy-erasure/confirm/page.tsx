import type { Metadata } from "next";
import { PrivacyErasureConfirm } from "../../../components/PrivacyErasure";

export const metadata: Metadata = { title: "Confirm account deletion", referrer: "no-referrer", robots: { index: false, follow: false } };
export default function PrivacyErasureConfirmPage() {
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-md"><PrivacyErasureConfirm /></div></main>;
}
