import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import Link from "next/link";
import { ArrowLeft, RefreshCcw } from "lucide-react";

export const metadata = {
  title: "Refund & Cancellation Policy - HireWiz",
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
        subtitle="Last updated: July 2026"
      />

      <FadeIn delay={0.1}>
        <GlassCard className="p-8 md:p-10 text-slate-300 leading-relaxed space-y-8">
          
          <div className="flex items-center gap-3 text-primary border-b border-slate-700/60 pb-4 mb-6">
            <RefreshCcw size={24} />
            <h2 className="text-xl font-bold text-white m-0">Overview</h2>
          </div>
          
          <p>
            At HireWiz ("we", "our", or "us"), we are committed to providing a transparent and fair experience for our users. This Refund and Cancellation Policy outlines the conditions under which refunds are provided and how you can cancel your subscription or digital purchases.
          </p>

          <h3 className="text-lg font-bold text-white mt-8 mb-4">1. Digital Goods and Services</h3>
          <p>
            HireWiz provides digital products, SaaS (Software as a Service) subscriptions, and AI-generated content (including resume parsing, job matching, and analytics). Because these are digital goods that are instantly accessed and consumed upon purchase, they are generally non-refundable unless otherwise specified by law or within this policy.
          </p>

          <h3 className="text-lg font-bold text-white mt-8 mb-4">2. Cancellation of Subscriptions</h3>
          <p>
            You may cancel your HireWiz subscription or recurring billing plan at any time through your account dashboard or by contacting our support team. 
          </p>
          <ul className="list-disc pl-6 space-y-2 mt-4 text-slate-400">
            <li>Cancellations will take effect at the end of your current paid billing cycle.</li>
            <li>You will retain access to the paid features until the billing cycle expires.</li>
            <li>We do not provide prorated refunds for mid-cycle cancellations.</li>
          </ul>

          <h3 className="text-lg font-bold text-white mt-8 mb-4">3. Eligibility for Refunds</h3>
          <p>
            While digital goods are generally non-refundable, we may issue refunds in the following exceptional circumstances:
          </p>
          <ul className="list-disc pl-6 space-y-2 mt-4 text-slate-400">
            <li><strong>Duplicate Charges:</strong> If you were accidentally charged twice for the same transaction due to a technical error.</li>
            <li><strong>Service Unavailability:</strong> If our core services were completely inaccessible for an extended period (exceeding 72 hours) immediately following your purchase.</li>
            <li><strong>Fraudulent Charges:</strong> If a purchase was made using your payment method without your authorization. (This will also be investigated by your bank and our payment processor, Razorpay).</li>
          </ul>

          <h3 className="text-lg font-bold text-white mt-8 mb-4">4. How to Request a Refund</h3>
          <p>
            If you believe you are eligible for a refund based on the criteria above, you must submit a request within <strong>7 days</strong> of the original transaction date.
          </p>
          <p className="mt-4">
            To request a refund, please email us at <strong>work@hirewizhq.com</strong> with the following information:
          </p>
          <ul className="list-disc pl-6 space-y-2 mt-4 text-slate-400">
            <li>Your full name and registered email address.</li>
            <li>Your order or transaction ID (provided in your receipt).</li>
            <li>A detailed explanation of why you are requesting a refund.</li>
          </ul>
          <p className="mt-4">
            Our team will review your request within 3-5 business days. If approved, the refund will be processed back to your original method of payment within 5-7 business days, depending on your bank's processing times.
          </p>

          <h3 className="text-lg font-bold text-white mt-8 mb-4">5. Chargebacks</h3>
          <p>
            If you initiate a chargeback or payment dispute with your credit card company or bank, we reserve the right to immediately suspend or terminate your account pending the resolution of the dispute. We highly encourage reaching out to us directly first so we can resolve any issues amicably.
          </p>

          <div className="mt-12 p-6 bg-slate-800/50 rounded-xl border border-slate-700/60">
            <h4 className="text-white font-bold mb-2">Contact Us for Payment Issues</h4>
            <p className="text-sm">
              If you have any questions regarding this policy, or if you are having trouble with a recent transaction, please reach out to our billing team at <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>.
            </p>
          </div>

        </GlassCard>
      </FadeIn>
    </main>
  );
}
