import type { Metadata } from "next";
import Link from "next/link";

import CookiePreferencesButton from "../../components/CookiePreferencesButton";
import PageHeader from "../../components/ui/PageHeader";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Cookie Policy",
  description: "The cookies and browser storage used by HireWiz, including consent and preference controls.",
  alternates: { canonical: "/cookies" },
};

const storageRows = [
  {
    name: "hirewiz_cookie_consent",
    owner: "HireWiz",
    type: "Local storage",
    purpose: "Stores whether you selected essential-only storage or allowed analytics so the consent prompt respects your choice.",
    category: "Essential preference",
    lifetime: "Up to 12 months",
  },
  {
    name: "next-auth.session-token / __Secure-next-auth.session-token",
    owner: "HireWiz (NextAuth)",
    type: "First-party cookie",
    purpose: "Maintains an authenticated session when you sign in through the website.",
    category: "Essential",
    lifetime: "Up to 30 days or earlier logout, depending on the sign-in flow",
  },
  {
    name: "next-auth.csrf-token / __Host-next-auth.csrf-token",
    owner: "HireWiz (NextAuth)",
    type: "First-party cookie",
    purpose: "Helps protect authentication requests against cross-site request forgery.",
    category: "Essential security",
    lifetime: "Session or short-lived",
  },
  {
    name: "next-auth.callback-url / __Secure-next-auth.callback-url",
    owner: "HireWiz (NextAuth)",
    type: "First-party cookie",
    purpose: "Returns you to an approved HireWiz page after authentication.",
    category: "Essential",
    lifetime: "Session or short-lived",
  },
  {
    name: "hirewiz_google_registration_consent",
    owner: "HireWiz",
    type: "First-party HttpOnly cookie",
    purpose: "Carries the current Terms, Privacy, and 18+ registration confirmation through an optional Google sign-in redirect.",
    category: "Essential registration record",
    lifetime: "Up to 10 minutes",
  },
  {
    name: "_ga",
    owner: "Google Analytics",
    type: "First-party analytics cookie",
    purpose: "Distinguishes browsers for aggregate website-usage measurement after you accept analytics.",
    category: "Optional analytics",
    lifetime: "Up to 2 years",
  },
  {
    name: "ph_<project-key>_posthog",
    owner: "PostHog",
    type: "First-party cookie and local storage",
    purpose: "Maintains consented product-analytics session and event attribution state.",
    category: "Optional analytics",
    lifetime: "Up to 12 months or earlier withdrawal",
  },
  {
    name: "_ga_<container-id>",
    owner: "Google Analytics",
    type: "First-party analytics cookie",
    purpose: "Maintains analytics session and campaign state after you accept analytics.",
    category: "Optional analytics",
    lifetime: "Up to 2 years",
  },
] as const;

export default function CookiePolicy() {
  return (
    <main className="flex min-h-screen flex-col items-center pb-20">
      <PageHeader
        badge="Legal"
        title="Cookie Policy"
        subtitle="The essential and optional browser storage used by HireWiz."
      />
      <div className="w-full max-w-5xl mx-auto px-6 md:px-8 mt-12 text-slate-300 space-y-8 leading-relaxed">
        <p className="text-sm text-slate-400">
          Effective: {SITE.policyEffectiveDate} · Last updated: {SITE.policyEffectiveDate} · Version {SITE.policyVersion}.
          HireWiz is operated by {SITE.operatorName}, trading as HireWiz. Questions: {" "}
          <a href={`mailto:${SITE.supportEmail}`} className="text-primary hover:underline">{SITE.supportEmail}</a>.
        </p>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">1. What this Policy covers</h2>
          <p>
            Cookies are small browser files. Local storage is a similar browser mechanism that can retain a value on
            your device. The table below describes storage that the current HireWiz frontend can use. Actual names may
            use a secure prefix when the website is served over HTTPS.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">2. Current inventory</h2>
          <div className="overflow-x-auto rounded-xl border border-slate-700/60">
            <table className="w-full min-w-[850px] text-left text-sm">
              <thead className="bg-slate-900/70 text-xs uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Owner / type</th>
                  <th className="px-4 py-3">Purpose</th>
                  <th className="px-4 py-3">Category</th>
                  <th className="px-4 py-3">Lifetime</th>
                </tr>
              </thead>
              <tbody>
                {storageRows.map((row) => (
                  <tr key={row.name} className="border-t border-slate-800 align-top">
                    <td className="px-4 py-4 font-semibold text-white break-words">{row.name}</td>
                    <td className="px-4 py-4 text-slate-400">{row.owner}<br />{row.type}</td>
                    <td className="px-4 py-4">{row.purpose}</td>
                    <td className="px-4 py-4 text-slate-400">{row.category}</td>
                    <td className="px-4 py-4 text-slate-400">{row.lifetime}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">3. Essential storage</h2>
          <p>
            Authentication, security, and consent-preference storage is necessary to sign in, protect requests, and
            remember your choice. Blocking or deleting it can sign you out or prevent account features from working.
            Backend access tokens are retained in the server-managed encrypted session and are not exposed to browser
            JavaScript or stored in browser local storage.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">4. Optional analytics</h2>
          <p>
            Google Analytics and PostHog load only when their keys are configured and you select “Accept analytics.”
            They are not loaded merely because you visit the site. Analytics measures aggregate acquisition and product
            use and is not used by HireWiz for targeted advertising. Choosing “Essential only” leaves analytics disabled.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">5. Authentication and checkout providers</h2>
          <p>
            If you choose Google sign-in or open a hosted payment checkout, that provider may set cookies on its own
            domain for authentication, fraud prevention, payment, and security. Those cookies are controlled by the
            provider and described in its privacy or cookie notice. The active payment provider is shown at checkout
            only after it has been approved and enabled.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">6. Change or withdraw your choice</h2>
          <p className="mb-4">
            You can reopen Cookie Preferences at any time. Withdrawing analytics consent prevents future analytics
            loading and removes accessible HireWiz-domain Google Analytics and PostHog storage. You can also clear site data in
            your browser; provider-domain cookies may need to be managed through that provider or your browser settings.
          </p>
          <CookiePreferencesButton />
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">7. Updates and contact</h2>
          <p>
            We will update this inventory when storage practices materially change. For questions, email {" "}
            <a href={`mailto:${SITE.supportEmail}`} className="text-primary hover:underline">{SITE.supportEmail}</a> or
            see the <Link href="/privacy" className="text-primary hover:underline">Privacy Policy</Link>.
          </p>
        </section>
      </div>
    </main>
  );
}
