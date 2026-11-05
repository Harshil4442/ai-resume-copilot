import type { Metadata } from "next";
import NotificationUnsubscribe from "../../../components/NotificationUnsubscribe";

export const metadata: Metadata = {
  title: "Email preferences", referrer: "no-referrer",
  robots: { index: false, follow: false, noarchive: true },
};

export default function UnsubscribePage() {
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-lg"><NotificationUnsubscribe /></div></main>;
}
