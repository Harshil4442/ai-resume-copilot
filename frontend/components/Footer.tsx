"use client";

import { Mail } from "lucide-react";
import Link from "next/link";

import { SITE } from "../lib/site";
import Logo from "./ui/Logo";

const links = [
  { href: "/pricing", label: "Pricing" },
  { href: "/tools", label: "Tools" },
  { href: "/resources", label: "Resources" },
  { href: "/blog", label: "Guides" },
  { href: "/contact", label: "Support" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/refund", label: "Refunds" },
];

export default function Footer() {
  return (
    <footer className="mt-auto border-t border-border bg-white py-12 sm:py-16">
      <div className="page-container grid gap-8 md:grid-cols-[1fr_auto] md:items-end">
        <div>
          <Link href="/" className="inline-flex items-center gap-2.5" aria-label="HireWiz home">
            <Logo />
            <span className="font-display text-[28px] leading-none text-foreground">HireWiz</span>
          </Link>
          <p className="mt-4 max-w-xl text-sm leading-6 text-muted-foreground">
            Evidence-backed career planning and application software. You review every recommendation and remain responsible for every submission.
          </p>
          <a href={`mailto:${SITE.supportEmail}`} className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-foreground hover:text-primary">
            <Mail size={15} aria-hidden="true" /> {SITE.supportEmail}
          </a>
        </div>
        <nav className="flex max-w-xl flex-wrap gap-x-5 gap-y-3 text-sm" aria-label="Footer navigation">
          {links.map((link) => (
            <Link key={link.href} href={link.href} className="text-muted-foreground underline-offset-4 transition-colors hover:text-primary hover:underline">
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="flex flex-col gap-2 border-t border-border pt-5 text-xs leading-5 text-muted-foreground md:col-span-2 md:flex-row md:justify-between">
          <span>HireWiz is operated by {SITE.operatorName}, {SITE.businessLocation}.</span>
          <span>Copyright {new Date().getFullYear()} HireWiz.</span>
        </div>
      </div>
    </footer>
  );
}
