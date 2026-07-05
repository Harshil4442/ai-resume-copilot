"use client";

import Link from "next/link";
import { Globe, Mail, MessageCircle } from "lucide-react";
import FadeIn from "./ui/FadeIn";

export default function Footer() {
  const currentYear = new Date().getFullYear();

  return (
    <footer className="w-full border-t border-slate-700/60 bg-surface/50 backdrop-blur-md pt-16 pb-8 mt-auto">
      <div className="max-w-[80rem] mx-auto px-6 md:px-8">
        <FadeIn direction="up" delay={0.1}>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-12 mb-12">
            <div>
              <Link href="/" className="flex items-center gap-2 mb-6">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-neutral-950 border border-slate-700/50 text-white text-xs font-black shadow-md">AI</span>
                <span className="text-sm font-black tracking-tight text-white">Resume CoPilot</span>
              </Link>
              <p className="text-sm text-slate-400 leading-relaxed mb-6">
                Your career signal, measured. Elevate your resume with AI-driven insights and land your dream job faster.
              </p>
              <div className="flex gap-4">
                <a href="#" className="text-slate-400 hover:text-primary transition-colors"><Globe size={20} /></a>
                <a href="#" className="text-slate-400 hover:text-primary transition-colors"><Mail size={20} /></a>
                <a href="#" className="text-slate-400 hover:text-primary transition-colors"><MessageCircle size={20} /></a>
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
              <h3 className="font-bold text-white mb-6">Resources</h3>
              <ul className="space-y-4">
                <li><Link href="/learning" className="text-sm text-slate-400 hover:text-white transition-colors">Learning Strategy</Link></li>
                <li><Link href="/billing" className="text-sm text-slate-400 hover:text-white transition-colors">Pricing</Link></li>
                <li><a href="#" className="text-sm text-slate-400 hover:text-white transition-colors">Documentation</a></li>
                <li><a href="#" className="text-sm text-slate-400 hover:text-white transition-colors">API References</a></li>
              </ul>
            </div>

            <div>
              <h3 className="font-bold text-white mb-6">Legal</h3>
              <ul className="space-y-4">
                <li><a href="#" className="text-sm text-slate-400 hover:text-white transition-colors">Privacy Policy</a></li>
                <li><a href="#" className="text-sm text-slate-400 hover:text-white transition-colors">Terms of Service</a></li>
                <li><a href="#" className="text-sm text-slate-400 hover:text-white transition-colors">Cookie Policy</a></li>
              </ul>
            </div>
          </div>
          
          <div className="flex flex-col md:flex-row items-center justify-between pt-8 border-t border-slate-700/60">
            <p className="text-sm text-slate-400">
              © {currentYear} AI Resume CoPilot. All rights reserved.
            </p>
          </div>
        </FadeIn>
      </div>
    </footer>
  );
}
