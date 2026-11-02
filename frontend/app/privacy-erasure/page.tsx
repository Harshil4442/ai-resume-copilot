import type { Metadata } from "next";
import { PrivacyErasureRequest } from "../../components/PrivacyErasure";

export const metadata: Metadata = { title: "Finish account deletion", referrer: "no-referrer", robots: { index: false, follow: false } };
export default function PrivacyErasurePage() {
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-md"><PrivacyErasureRequest /></div></main>;
}
