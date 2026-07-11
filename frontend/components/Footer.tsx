"use client";

import Link from "next/link";
import { Globe, Mail, MessageCircle } from "lucide-react";
import FadeIn from "./ui/FadeIn";
import Logo from "./ui/Logo";
import Image from "next/image";

export default function Footer() {
  const currentYear = new Date().getFullYear();

  return (
    <footer className="w-full border-t border-slate-700/60 bg-surface/50 backdrop-blur-md pt-16 pb-8 mt-auto">
      <div className="max-w-[80rem] mx-auto px-6 md:px-8">
        <FadeIn direction="up" delay={0.1}>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-12 mb-12">
            <div>
              <Link href="/" className="flex items-center gap-2.5 mb-6">
                {/* SVG Logo */}
                <Logo />
                
                {/* Alternative PNG Logo (Uncomment to use the uploaded image asset directly) */}
                {/* 
                <Image 
                  src="/logo.png" 
                  alt="HireWiz Logo" 
                  width={34} 
                  height={34} 
                  className="object-contain"
                /> 
                */}
                
                <span className="text-sm font-black tracking-tight text-white">HireWiz</span>
              </Link>
              <p className="text-sm text-slate-400 leading-relaxed mb-6">
                Self-service, AI-assisted software that helps you review, structure, and improve your own resume — with compatibility estimates, skill-gap analysis, and tailored learning suggestions you stay in control of.
              </p>
              <div className="flex gap-4">
                <a href="https://www.hirewizhq.com" target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-primary transition-colors"><Globe size={20} /></a>
                <a href="mailto:work@hirewizhq.com" className="text-slate-400 hover:text-primary transition-colors"><Mail size={20} /></a>
                <a href="mailto:work@hirewizhq.com" className="text-slate-400 hover:text-primary transition-colors"><MessageCircle size={20} /></a>
              </div>
            </div>

            <div>
              <h3 className="font-bold text-white mb-6">Product</h3>
              <ul className="space-y-4">
                <li><Link href="/dashboard" className="text-sm text-slate-400 hover:text-white transition-colors">Dashboard</Link></li>
                <li><Link href="/resume" className="text-sm text-slate-400 hover:text-white transition-colors">Resume Parser</Link></li>
                <li><Link href="/jobs" className="text-sm text-slate-400 hover:text-white transition-colors">Job Match</Link></li>
                <li><Link href="/market" className="text-sm text-slate-400 hover:text-white transition-colors">Market Trends</Link></li>
              </ul>
            </div>

            <div>
              <h3 className="font-bold text-white mb-6">Company</h3>
              <ul className="space-y-4">
                <li><Link href="/pricing" className="text-sm text-slate-400 hover:text-white transition-colors">Pricing</Link></li>
                <li><Link href="/about" className="text-sm text-slate-400 hover:text-white transition-colors">About & How It Works</Link></li>
                <li><Link href="/subprocessors" className="text-sm text-slate-400 hover:text-white transition-colors">Service Providers</Link></li>
                <li><Link href="/contact" className="text-sm text-slate-400 hover:text-white transition-colors">Contact Us</Link></li>
              </ul>
            </div>

            <div>
              <h3 className="font-bold text-white mb-6">Legal</h3>
              <ul className="space-y-4">
                <li><Link href="/privacy" className="text-sm text-slate-400 hover:text-white transition-colors">Privacy Policy</Link></li>
                <li><Link href="/terms" className="text-sm text-slate-400 hover:text-white transition-colors">Terms of Service</Link></li>
                <li><Link href="/refund" className="text-sm text-slate-400 hover:text-white transition-colors">Refund & Cancellation</Link></li>
                <li><Link href="/digital-delivery" className="text-sm text-slate-400 hover:text-white transition-colors">Digital Delivery</Link></li>
                <li><Link href="/cookies" className="text-sm text-slate-400 hover:text-white transition-colors">Cookie Policy</Link></li>
              </ul>
            </div>
          </div>
          
          <div className="flex flex-col md:flex-row items-center justify-between pt-8 border-t border-slate-700/60">
            <p className="text-sm text-slate-400">
              © {currentYear} HireWiz. All rights reserved.
            </p>
          </div>
        </FadeIn>
      </div>
    </footer>
  );
}
