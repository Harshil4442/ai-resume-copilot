import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import Link from "next/link";
import { ArrowLeft, Mail, MapPin, MessageCircle, Clock, Phone, ShieldCheck } from "lucide-react";
import type { Metadata } from "next";
import { SITE } from "../../lib/site";

export const metadata: Metadata = {
  title: "Contact, Billing Support & Grievance",
  description: "HireWiz support, billing, privacy, business-location, and grievance contact details.",
  alternates: { canonical: "/contact" },
};

export default function ContactPage() {
  return (
    <main className="w-full max-w-[50rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-8">
      <Link href="/" className="inline-flex items-center text-sm font-semibold text-slate-400 hover:text-primary transition-colors">
        <ArrowLeft size={16} className="mr-2" /> Back to Home
      </Link>
      
      <PageHeader 
        badge="Get in Touch"
        title="Contact Us"
        subtitle="Support for accounts, billing, payments, privacy requests, and grievances."
      />

      <FadeIn delay={0.1}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <GlassCard className="p-8">
            <div className="p-3 bg-primary/10 text-primary w-fit rounded-lg mb-6">
              <Mail size={24} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">Email Support</h3>
            <p className="text-sm text-slate-400 mb-6">
              Our primary support channel. We aim to respond within 24–48 business hours.
            </p>
            <a href={`mailto:${SITE.supportEmail}`} className="text-primary font-semibold hover:underline text-lg">
              {SITE.supportEmail}
            </a>
          </GlassCard>

          <GlassCard className="p-8">
            <div className="p-3 bg-purple-500/10 text-purple-400 w-fit rounded-lg mb-6">
              <Clock size={24} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">Phone & Business Hours</h3>
            <p className="text-sm text-slate-400 mb-4">
              Available during standard business hours, Indian Standard Time (IST). Email is the fastest way to reach us.
            </p>
            <a href={SITE.supportPhoneHref} className="inline-flex items-center gap-2 text-primary font-semibold hover:underline mb-3">
              <Phone size={16} /> {SITE.supportPhoneDisplay}
            </a>
            <div className="text-slate-300 font-semibold text-sm">
              {SITE.supportHours}
            </div>
          </GlassCard>

          <GlassCard className="p-8 md:col-span-2">
            <div className="p-3 bg-emerald-500/10 text-emerald-400 w-fit rounded-lg mb-6">
              <MapPin size={24} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">Operator &amp; Business Location</h3>
            <p className="text-sm text-slate-400 mb-6 max-w-xl">
              HireWiz is operated by the person named below. Customer support is provided remotely; no walk-in or in-person support is offered.
            </p>
            <address className="not-italic text-slate-300 leading-relaxed font-medium">
              {SITE.operatorName}<br />
              trading as HireWiz<br />
              {SITE.businessLocation}
            </address>
          </GlassCard>

          <GlassCard className="p-8 md:col-span-2">
            <div className="p-3 bg-blue-500/10 text-blue-400 w-fit rounded-lg mb-6">
              <ShieldCheck size={24} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">Grievance Officer</h3>
            <p className="text-sm text-slate-400 mb-6 max-w-xl">
              You may raise an account, billing, privacy, or service complaint with the Grievance Officer. We aim to
              acknowledge a complete complaint within 48 hours and resolve it within 30 days, subject to its complexity
              and any information required from you or a payment provider.
            </p>
            <div className="text-slate-300 leading-relaxed font-medium text-sm">
              <div className="font-bold text-white">{SITE.grievanceContactName}</div>
              <div className="text-slate-400">{SITE.grievanceContactRole}</div>
              <div className="mt-2 flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4">
                <a href={`mailto:${SITE.supportEmail}`} className="inline-flex items-center gap-2 text-primary hover:underline">
                  <Mail size={14} /> {SITE.supportEmail}
                </a>
                <a href={SITE.supportPhoneHref} className="inline-flex items-center gap-2 text-primary hover:underline">
                  <Phone size={14} /> {SITE.supportPhoneDisplay}
                </a>
              </div>
            </div>
          </GlassCard>
        </div>

        <div className="mt-8 p-6 bg-slate-900/50 rounded-xl border border-slate-700/60 flex items-center justify-between gap-6 flex-col sm:flex-row text-center sm:text-left">
          <div>
            <h4 className="text-white font-bold mb-1 flex items-center justify-center sm:justify-start gap-2">
              <MessageCircle size={18} className="text-primary" /> Payment or Billing Issue?
            </h4>
            <p className="text-sm text-slate-400">
              If you have an urgent inquiry regarding a payment, failed transaction, or charge, please include your transaction ID in the subject line of your email.
            </p>
          </div>
          <a href={`mailto:${SITE.supportEmail}?subject=HireWiz%20billing%20support`} className="whitespace-nowrap px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-sm font-semibold rounded-lg transition-colors border border-slate-600">
            Email Billing
          </a>
        </div>
      </FadeIn>
    </main>
  );
}
