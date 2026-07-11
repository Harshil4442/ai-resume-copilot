import type { Metadata } from "next";
import Link from "next/link";

import PageHeader from "../../components/ui/PageHeader";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "Terms governing access to and use of the HireWiz self-service software platform.",
  alternates: { canonical: "/terms" },
};

export default function TermsOfService() {
  return (
    <main className="flex min-h-screen flex-col items-center pb-20">
      <PageHeader
        badge="Legal"
        title="Terms of Service"
        subtitle="The terms for using HireWiz and purchasing digital access."
      />
      <div className="w-full max-w-4xl mx-auto px-6 md:px-8 mt-12 text-slate-300 space-y-8 leading-relaxed">
        <p className="text-sm text-slate-400">
          Effective: {SITE.policyEffectiveDate} · Last updated: {SITE.policyEffectiveDate} · Version {SITE.policyVersion}.
          HireWiz is operated by {SITE.operatorName}, trading as HireWiz, in Gujarat, India ("HireWiz", "we", "our", or "us").
        </p>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">1. Acceptance and eligibility</h2>
          <p>
            By creating an account, using the service, or making a purchase, you agree to these Terms and acknowledge
            our <Link href="/privacy" className="text-primary hover:underline">Privacy Policy</Link>. You must be at
            least 18 years old and legally able to enter into this agreement. If you do not agree, do not use HireWiz.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">2. What the service is</h2>
          <p>
            HireWiz is automated, self-service software. You provide your own resume and, optionally, job-description
            text. The service can parse resume text, calculate HireWiz compatibility estimates, identify possible
            skill gaps, analyze a sample of job-posting data, and generate wording, learning, and project suggestions.
          </p>
          <p className="mt-4">
            HireWiz is not a recruitment agency, staffing company, job-placement service, job board, human coaching
            service, or automated application service. We do not make employment decisions, submit applications,
            recruit or place candidates, sell candidate databases, or guarantee ATS acceptance, interviews, offers,
            or employment. HireWiz estimates are not scores produced by an employer or a specific applicant-tracking system.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">3. Accounts and account security</h2>
          <p>
            You must provide accurate account information, protect your credentials, and promptly tell us about
            suspected unauthorized access. You are responsible for activity under your account unless applicable law
            provides otherwise. Account access may be limited while we investigate fraud, security, payment disputes,
            prohibited use, or a material breach of these Terms.
          </p>
          <p className="mt-4">
            You can request account deletion through your Profile or by contacting support. Deletion consequences and
            records that may need to be retained are described in the Privacy Policy.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">4. Public price and paid access</h2>
          <p>
            The current paid product is shown on the public <Link href="/pricing" className="text-primary hover:underline">Pricing page</Link>.
            For the India launch, HireWiz offers a one-time Premium pass that provides 30 days of access. It is not a
            recurring subscription, does not create an automatic-renewal mandate, and does not automatically charge
            you again when it expires. HireWiz does not currently sell standalone analysis-unit packs.
          </p>
          <ul className="list-disc pl-6 mt-4 space-y-2">
            <li>The final order summary shows the product, INR total, tax treatment, duration, and renewal status before payment.</li>
            <li>You authorize the payment processor used for checkout to charge the payment method you select.</li>
            <li>HireWiz does not collect or store raw card details, CVV, UPI PIN, or bank-login credentials.</li>
            <li>Access is granted only after payment confirmation and may be paused for fraud, mismatch, refund, or dispute review.</li>
            <li>Failed or pending payments do not create paid access. Receipts and transaction records may be retained for support, reconciliation, and legal compliance.</li>
          </ul>
          <p className="mt-4">
            Delivery, cancellation, and refund rules are incorporated through our{" "}
            <Link href="/digital-delivery" className="text-primary hover:underline">Digital Service Delivery &amp; Shipping Policy</Link>{" "}
            and <Link href="/refund" className="text-primary hover:underline">Refund &amp; Cancellation Policy</Link>.
            Nothing in these Terms limits rights that cannot lawfully be waived.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">5. Analysis units</h2>
          <p>
            New free accounts receive 20 complimentary analysis units for specified AI-assisted operations. The
            unit cost is displayed before a metered operation starts. Units are a feature-use allowance only: they are
            not money, stored value, virtual currency, or a payment instrument; they have no cash value and cannot be
            transferred, withdrawn, resold, or redeemed. They do not refresh or expire while the account remains open,
            and unused units are removed with account deletion. Premium users do not incur unit deductions while their pass is active.
          </p>
          <p className="mt-4">
            If a technical failure consumes units without delivering a result, contact support with enough information
            for us to review the event. We do not promise automatic restoration unless the product interface expressly says so.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">6. Your content and permission to process it</h2>
          <p>
            You retain your rights in content you upload. You grant HireWiz and the service providers listed on our{" "}
            <Link href="/subprocessors" className="text-primary hover:underline">Service Providers page</Link> a limited,
            non-exclusive permission to host, transmit, parse, and otherwise process that content only to provide,
            secure, troubleshoot, and support the features you request, subject to the Privacy Policy. You represent
            that you own the content or have authority to process it through HireWiz.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">7. AI output and user verification</h2>
          <p>
            AI output may be incomplete, inaccurate, biased, outdated, or fabricated. You must verify every suggested
            fact before saving, exporting, or sharing it. Do not add an employer, date, degree, certification, skill,
            achievement, or metric unless it is true and you can support it. You remain responsible for your resume,
            applications, decisions, and use of any output.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">8. Acceptable use</h2>
          <p>You must not use HireWiz to:</p>
          <ul className="list-disc pl-6 mt-4 space-y-2">
            <li>upload another person's resume or personal data without authority;</li>
            <li>impersonate someone, fabricate credentials or achievements, or misrepresent qualifications;</li>
            <li>cheat on an exam, assessment, interview, hiring process, or background check;</li>
            <li>upload malware or harmful content, probe security, bypass access controls, or disrupt the service;</li>
            <li>scrape, reverse-engineer, resell, or systematically extract the service or third-party data except where law expressly permits it;</li>
            <li>infringe intellectual-property, privacy, confidentiality, or other rights; or</li>
            <li>use the service for unlawful, deceptive, discriminatory, or abusive activity.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">9. Market and third-party information</h2>
          <p>
            Market results use a limited sample made available by third-party data providers. Coverage, freshness,
            geography, sample size, and availability vary. Percentages and summaries are informational estimates and
            do not represent the entire labor market. Links and third-party services are governed by their own terms,
            and HireWiz does not control their content or continued availability.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">10. HireWiz intellectual property</h2>
          <p>
            HireWiz and its licensors retain rights in the software, design, branding, and proprietary methods. These
            Terms give you a personal, limited, revocable, non-transferable right to use the service for its intended
            purpose; they do not transfer ownership of the platform or third-party materials.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">11. Availability and changes</h2>
          <p>
            The service is provided on an "as available" basis. We may maintain, secure, change, or discontinue
            features. Where a material change affects an active paid pass, we will act reasonably and apply the Refund
            Policy and applicable law. Events outside reasonable control may delay or interrupt the service.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">12. Disclaimers and limitation of liability</h2>
          <p>
            To the extent permitted by law, HireWiz does not warrant that AI output will be accurate, that every file
            will parse correctly, or that use of the service will produce a career or employment outcome. We do not
            exclude liability or consumer remedies that applicable law does not allow us to exclude.
          </p>
          <p className="mt-4">
            To the extent a limitation is lawful, HireWiz is not responsible for indirect or consequential loss that
            was not reasonably foreseeable. For a claim directly relating to paid access, aggregate liability is
            limited to the amount you paid for the affected pass. This limitation does not apply to fraud, wilful
            misconduct, or any liability that cannot be limited by law.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">13. Responsibility for misuse</h2>
          <p>
            To the extent permitted by law, you are responsible for direct losses and third-party claims caused by
            your unlawful use, infringement, fraud, or deliberate breach of these Terms. We will provide reasonable
            notice of a claim where possible and will not settle a claim imposing a non-monetary obligation on you without consent.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">14. Governing law and complaints</h2>
          <p>
            These Terms are governed by the laws of India, subject to mandatory consumer protections and the
            jurisdiction rules that apply to you. Before starting formal proceedings, please contact our Grievance
            Officer through the <Link href="/contact" className="text-primary hover:underline">Contact page</Link> so
            we can try to resolve the concern. This does not prevent you from using a regulator, consumer forum,
            payment dispute process, or court where you have a legal right to do so.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">15. General terms</h2>
          <p>
            If part of these Terms is unenforceable, the remaining provisions continue to apply. Delay in enforcing a
            right is not a waiver. You may not transfer your account or these Terms without our consent. We may assign
            these Terms as part of a genuine business reorganization while preserving applicable consumer rights.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">16. Changes to these Terms</h2>
          <p>
            We may update these Terms to reflect product, legal, or operational changes. We will update the date and
            version above and provide additional notice when a change is material. Changes do not retroactively remove
            rights that have already accrued.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">17. Contact</h2>
          <p>
            Email <a href={`mailto:${SITE.supportEmail}`} className="text-primary hover:underline">{SITE.supportEmail}</a>,
            call <a href={SITE.supportPhoneHref} className="text-primary hover:underline">{SITE.supportPhoneDisplay}</a>,
            or use the <Link href="/contact" className="text-primary hover:underline">support and grievance details</Link>.
          </p>
        </section>
      </div>
    </main>
  );
}
