import { redirect } from "next/navigation";

export const metadata = { title: "Recover your account", robots: { index: false, follow: false }, referrer: "no-referrer" };
export default function AccountRecoveryPage() { redirect("/forgot-password"); }
