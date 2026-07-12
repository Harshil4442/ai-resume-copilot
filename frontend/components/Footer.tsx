"use client";

import Link from "next/link";
import { Globe, Mail, Phone } from "lucide-react";
import FadeIn from "./ui/FadeIn";
import Logo from "./ui/Logo";
import { SITE } from "../lib/site";

export default function Footer() {
  const currentYear = new Date().getFullYear();

  return (
    <footer className="w-full border-t border-slate-700/60 bg-surface/50 backdrop-blur-md pt-16 pb-8 mt-auto">
      <div className="max-w-[80rem] mx-auto px-6 md:px-8">
        <FadeIn direction="up" delay={0.1}>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-12 mb-12">
            <div>
              <Link href="/" className="flex items-center gap-2.5 mb-6">
                <Logo />
                <span className="text-sm font-black tracking-tight text-white">HireWiz</span>
              </Link>
              <p className="text-sm text-slate-400 leading-relaxed mb-6">
                Self-service, AI-assisted software that helps you review, structure, and improve your own resume — with compatibility estimates, skill-gap analysis, and tailored learning suggestions you stay in control of.
              </p>
              <div className="flex gap-4">
                <a aria-label="HireWiz website" href={SITE.canonicalUrl} className="text-slate-400 hover:text-primary transition-colors"><Globe size={20} /></a>
                <a aria-label="Email HireWiz support" href={`mailto:${SITE.supportEmail}`} className="text-slate-400 hover:text-primary transition-colors"><Mail size={20} /></a>
                <a aria-label="Call HireWiz support" href={SITE.supportPhoneHref} className="text-slate-400 hover:text-primary transition-colors"><Phone size={20} /></a>
              </div>
            </div>

            <div>
              <h3 className="font-bold text-white mb-6">Product</h3>
              <ul className="space-y-4">
                <li><Link href="/tools" className="text-sm text-slate-400 hover:text-white transition-colors">Free Tools</Link></li>
                <li><Link href="/tools/resume-bullet-optimizer" className="text-sm text-slate-400 hover:text-white transition-colors">Bullet Optimizer</Link></li>
                <li><Link href="/about" className="text-sm text-slate-400 hover:text-white transition-colors">How It Works</Link></li>
                <li><Link href="/pricing" className="text-sm text-slate-400 hover:text-white transition-colors">Pricing</Link></li>
              </ul>
            </div>

            <div>
              <h3 className="font-bold text-white mb-6">Guides</h3>
              <ul className="space-y-4">
                <li><Link href="/blog" className="text-sm text-slate-400 hover:text-white transition-colors">Resume Guides</Link></li>
                <li><Link href="/blog/ats-resume-format-for-freshers" className="text-sm text-slate-400 hover:text-white transition-colors">Fresher Resume Format</Link></li>
                <li><Link href="/blog/how-to-match-resume-with-job-description" className="text-sm text-slate-400 hover:text-white transition-colors">JD Match Guide</Link></li>
                <li><Link href="/resources" className="text-sm text-slate-400 hover:text-white transition-colors">Learning Resources</Link></li>
              </ul>
            </div>

            <div>
              <h3 className="font-bold text-white mb-6">Legal</h3>
              <ul className="space-y-4">
                <li><Link href="/contact" className="text-sm text-slate-400 hover:text-white transition-colors">Contact Us</Link></li>
                <li><Link href="/digital-delivery" className="text-sm text-slate-400 hover:text-white transition-colors">Delivery &amp; Shipping</Link></li>
                <li><Link href="/refund" className="text-sm text-slate-400 hover:text-white transition-colors">Refund &amp; Cancellation</Link></li>
                <li><Link href="/privacy" className="text-sm text-slate-400 hover:text-white transition-colors">Privacy Policy</Link></li>
                <li><Link href="/terms" className="text-sm text-slate-400 hover:text-white transition-colors">Terms of Service</Link></li>
              </ul>
            </div>
          </div>
          
          <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between pt-8 border-t border-slate-700/60">
            <div className="text-xs text-slate-400 leading-relaxed text-center md:text-left">
              <p>HireWiz is operated by {SITE.operatorName}, trading as HireWiz.</p>
              <p>{SITE.businessLocation} · <a href={`mailto:${SITE.supportEmail}`} className="hover:text-primary">{SITE.supportEmail}</a> · <a href={SITE.supportPhoneHref} className="hover:text-primary">{SITE.supportPhoneDisplay}</a></p>
            </div>
            <p className="text-sm text-slate-400 whitespace-nowrap">
              © {currentYear} HireWiz. All rights reserved.
            </p>
          </div>
        </FadeIn>
      </div>
    </footer>
  );
}
