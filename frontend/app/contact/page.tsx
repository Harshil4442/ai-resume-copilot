import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import FadeIn from "../../components/ui/FadeIn";
import Link from "next/link";
import { ArrowLeft, Mail, MapPin, MessageCircle, Clock } from "lucide-react";

export const metadata = {
  title: "Contact Us - HireWiz",
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
        subtitle="We're here to help. Reach out to us for support, billing questions, or partnerships."
      />

      <FadeIn delay={0.1}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <GlassCard className="p-8">
            <div className="p-3 bg-primary/10 text-primary w-fit rounded-lg mb-6">
              <Mail size={24} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">Email Support</h3>
            <p className="text-sm text-slate-400 mb-6">
              Our primary method of support. We aim to respond to all inquiries within 24-48 business hours.
            </p>
            <a href="mailto:work@hirewizhq.com" className="text-primary font-semibold hover:underline text-lg">
              work@hirewizhq.com
            </a>
          </GlassCard>

          <GlassCard className="p-8">
            <div className="p-3 bg-purple-500/10 text-purple-400 w-fit rounded-lg mb-6">
              <Clock size={24} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">Business Hours</h3>
            <p className="text-sm text-slate-400 mb-6">
              Our support team is available during standard business hours in Indian Standard Time (IST).
            </p>
            <div className="text-slate-300 font-semibold">
              Monday - Sunday<br/>
              10:00 AM - 10:00 PM IST
            </div>
          </GlassCard>

          <GlassCard className="p-8 md:col-span-2">
            <div className="p-3 bg-emerald-500/10 text-emerald-400 w-fit rounded-lg mb-6">
              <MapPin size={24} />
            </div>
            <h3 className="text-xl font-bold text-white mb-2">Registered Address</h3>
            <p className="text-sm text-slate-400 mb-6 max-w-xl">
              This is our registered administrative office. Please note that we do not offer in-person support at this location. All support requests must be routed via email.
            </p>
            <address className="not-italic text-slate-300 leading-relaxed font-medium">
              HireWiz Technologies<br />
              Surat, Gujarat<br />
              India, 395010
            </address>
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
          <a href="mailto:work@hirewizhq.com?subject=Billing%20Support:%20[Insert%20Transaction%20ID]" className="whitespace-nowrap px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-sm font-semibold rounded-lg transition-colors border border-slate-600">
            Email Billing
          </a>
        </div>
      </FadeIn>
    </main>
  );
}
