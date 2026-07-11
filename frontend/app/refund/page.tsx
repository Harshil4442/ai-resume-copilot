import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, RefreshCcw } from "lucide-react";

import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Refund & Cancellation Policy",
  description: "Refund eligibility, cancellation, review, and payment-dispute information for HireWiz digital access.",
  alternates: { canonical: "/refund" },
};

export default function RefundPolicyPage() {
  return (
    <main className="w-full max-w-[50rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-8">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>

      <PageHeader
        badge="Legal & Compliance"
        title="Refund & Cancellation Policy"
        subtitle={`Effective and last updated: ${SITE.policyEffectiveDate}`}
      />

      <FadeIn delay={0.1}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-8" hoverEffect={false}>
          <div className="flex items-center gap-3 text-primary border-b border-slate-700/60 pb-4">
            <RefreshCcw size={24} />
            <h2 className="text-xl font-bold text-white m-0">Transparent rules for digital access</h2>
          </div>

          <p className="text-sm text-slate-400 m-0">
            Version {SITE.policyVersion}. HireWiz is operated by {SITE.operatorName}, trading as HireWiz. This Policy
            does not limit any refund, dispute, or consumer right that cannot lawfully be waived.
          </p>

          <section>
            <h3 className="text-lg font-bold text-white mb-4">1. Product covered</h3>
            <p>
              HireWiz currently sells one digital product for customers in India: a one-time Premium pass providing
              30 days of access. It is not an automatically renewing subscription. HireWiz does not currently sell
              standalone analysis-unit packs. Access is delivered to the purchasing account after confirmed payment;
              no physical goods are shipped.
            </p>
          </section>

          <section>
            <h3 className="text-lg font-bold text-white mb-4">2. Cancellation and expiry</h3>
            <ul className="list-disc pl-6 space-y-2 text-slate-400">
              <li>There is no recurring charge or renewal mandate to cancel. The pass expires at the end of its 30-day period unless you choose to buy another pass.</li>
              <li>You can end Premium access early through Profile or by contacting support. Ending access early does not by itself create a refund entitlement.</li>
              <li>Deleting your account ends access and removes active application data as described in the Privacy Policy; deletion does not automatically create a refund.</li>
            </ul>
          </section>

          <section>
            <h3 className="text-lg font-bold text-white mb-4">3. When a refund may be available</h3>
            <p>We will review a refund request where:</p>
            <ul className="list-disc pl-6 mt-4 space-y-2 text-slate-400">
              <li>the same order was charged more than once because of a technical or processing error;</li>
              <li>payment was confirmed but Premium access was not delivered, and support could not correct the activation;</li>
              <li>a material HireWiz service failure substantially prevented use of the paid pass and support could not provide a reasonable remedy;</li>
              <li>the payment was unauthorized, subject to reasonable identity, account, and payment-provider review; or</li>
              <li>a refund is required by applicable law or expressly offered in the final order terms.</li>
            </ul>
            <p className="mt-4">
              Because access is digital and begins after payment confirmation, a change of mind, unused time, or
              dissatisfaction with informational AI output is generally not refundable after access is delivered,
              except where applicable law or the circumstances above require otherwise.
            </p>
          </section>

          <section>
            <h3 className="text-lg font-bold text-white mb-4">4. How to request a refund</h3>
            <p>
              Email <a href={`mailto:${SITE.supportEmail}`} className="text-primary hover:underline">{SITE.supportEmail}</a>{" "}
              from your registered email address within seven days of the charge, or as soon as reasonably possible
              for an unauthorized charge or legal claim. Include:
            </p>
            <ul className="list-disc pl-6 mt-4 space-y-2 text-slate-400">
              <li>your registered email address;</li>
              <li>the order or transaction ID shown in the receipt or checkout confirmation;</li>
              <li>the charge date and amount; and</li>
              <li>a concise explanation and any relevant error details.</li>
            </ul>
            <p className="mt-4">
              We aim to review a complete request and communicate a decision within three to five business days. We may
              ask for limited additional information needed to verify the account, transaction, or claimed failure.
            </p>
          </section>

          <section>
            <h3 className="text-lg font-bold text-white mb-4">5. Approved refunds</h3>
            <p>
              An approved refund is initiated to the original payment method through the processor used for checkout.
              HireWiz will communicate when it has initiated the refund; the bank, card network, UPI app, or processor
              controls when the amount is finally posted. Their timing may vary and is outside HireWiz's control.
            </p>
            <p className="mt-4">
              The refund amount will follow applicable law and the approved decision. Taxes and processing adjustments,
              if any, will be handled according to the original transaction, applicable law, and processor rules.
              HireWiz does not promise reimbursement of exchange-rate differences or third-party bank charges.
            </p>
          </section>

          <section>
            <h3 className="text-lg font-bold text-white mb-4">6. Payment disputes and chargebacks</h3>
            <p>
              You may use any bank, payment-provider, regulator, or consumer-dispute right available to you. If a
              dispute is opened, we may temporarily pause the affected paid access while the transaction is reviewed
              and provide relevant order, delivery, and account records to the payment provider. We will not suspend
              unrelated rights in retaliation for a good-faith dispute. Contacting us first may allow a faster resolution.
            </p>
          </section>

          <section>
            <h3 className="text-lg font-bold text-white mb-4">7. Billing and grievance contact</h3>
            <p>
              Billing support: <a href={`mailto:${SITE.supportEmail}`} className="text-primary hover:underline">{SITE.supportEmail}</a>.
              If the response does not resolve your concern, contact {SITE.grievanceContactName}, {SITE.grievanceContactRole},
              using the details on the <Link href="/contact" className="text-primary hover:underline">Contact page</Link>.
            </p>
          </section>

          <div className="pt-5 border-t border-slate-700/60 flex flex-wrap gap-4 text-xs font-semibold text-slate-400">
            <Link href="/pricing" className="hover:text-primary">Pricing</Link>
            <Link href="/digital-delivery" className="hover:text-primary">Digital Delivery &amp; Shipping</Link>
            <Link href="/terms" className="hover:text-primary">Terms of Service</Link>
            <Link href="/privacy" className="hover:text-primary">Privacy Policy</Link>
          </div>
        </GlassCard>
      </FadeIn>
    </main>
  );
}
