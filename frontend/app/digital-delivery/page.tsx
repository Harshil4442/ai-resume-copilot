import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import Link from "next/link";
import { ArrowLeft, Package } from "lucide-react";

export const metadata = {
  title: "Digital Service Delivery & Shipping Policy — HireWiz",
  description:
    "HireWiz delivers digital software access only; no physical goods are shipped. How and when access is delivered after payment.",
};

export default function DigitalDeliveryPage() {
  return (
    <main className="w-full max-w-[50rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-8">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Legal & Compliance"
        title="Digital Service Delivery & Shipping Policy"
        subtitle="Last updated: 11 July 2026"
      />

      <FadeIn delay={0.1}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-8" hoverEffect={false}>
          <div className="flex items-center gap-3 text-primary border-b border-slate-700/60 pb-4">
            <Package size={24} />
            <h2 className="text-xl font-bold text-white m-0">Digital product — no physical shipping</h2>
          </div>

          <p className="text-sm text-slate-400 m-0">
            HireWiz is operated by SAVALIYA HARSHIL YOGESHBHAI, an individual trading as HireWiz.
          </p>

          <p>
            HireWiz provides access to <strong>digital, self-service software only</strong>. No physical goods are sold
            or shipped, and there are no shipping charges, couriers, or delivery addresses involved.
          </p>

          <div>
            <h3 className="text-lg font-bold text-white mb-3">1. What you receive</h3>
            <ul className="list-disc pl-6 space-y-2 text-slate-400">
              <li><strong>Premium subscription:</strong> your account is upgraded to Premium, unlocking unlimited AI-assisted operations for the billing month.</li>
              <li><strong>Analysis Units Pack:</strong> the purchased analysis units are added to your account balance.</li>
            </ul>
          </div>

          <div>
            <h3 className="text-lg font-bold text-white mb-3">2. When access is delivered</h3>
            <p>
              Access and unit allowances are activated <strong>only after your payment is confirmed</strong>. In normal
              conditions this is immediate. If confirmation is delayed by the payment provider, activation typically
              completes within a few minutes and, in rare cases, up to a few hours.
            </p>
          </div>

          <div>
            <h3 className="text-lg font-bold text-white mb-3">3. Where it is delivered</h3>
            <p>
              Delivery is entirely online, inside your HireWiz account associated with your registered email address.
              A payment confirmation is shown on-screen after checkout.
            </p>
          </div>

          <div>
            <h3 className="text-lg font-bold text-white mb-3">4. If activation is delayed</h3>
            <p>
              If you have paid but do not see your Premium status or units within a few hours, email{" "}
              <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>{" "}
              from your registered email address with your transaction ID, and we will resolve it.
            </p>
          </div>

          <div>
            <h3 className="text-lg font-bold text-white mb-3">5. Availability</h3>
            <p>
              The service is provided on an "as available" basis and may occasionally be interrupted for maintenance or
              for reasons outside our control. See our{" "}
              <Link href="/terms" className="text-primary hover:underline">Terms of Service</Link>.
            </p>
          </div>

          <div className="pt-4 border-t border-slate-700/60 flex flex-wrap gap-x-4 gap-y-2 text-xs font-semibold text-slate-400">
            <Link href="/pricing" className="hover:text-primary">Pricing</Link>
            <Link href="/refund" className="hover:text-primary">Refund &amp; Cancellation</Link>
            <Link href="/contact" className="hover:text-primary">Contact</Link>
          </div>
        </GlassCard>
      </FadeIn>
    </main>
  );
}
