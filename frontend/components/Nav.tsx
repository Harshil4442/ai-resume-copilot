"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BriefcaseBusiness,
  CircleGauge,
  CreditCard,
  FileText,
  LayoutDashboard,
  Library,
  LogOut,
  Menu,
  Search,
  User,
  Wrench,
  X,
} from "lucide-react";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { apiGet } from "../lib/api";
import Logo from "./ui/Logo";

type ProfileSummary = {
  ai_credits: number;
  tier: string;
};
type FeatureResponse = {
  features: Record<string, { enabled: boolean }>;
};
type NavLink = {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  feature?: string;
};

const appLinks: NavLink[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/workspace", label: "Workspace", icon: BriefcaseBusiness, feature: "career_workspace" },
  { href: "/resume", label: "Resume", icon: FileText },
  { href: "/market", label: "Market", icon: Search },
  { href: "/profile", label: "Profile", icon: User },
  { href: "/billing", label: "Billing", icon: CreditCard },
];

const publicLinks: NavLink[] = [
  { href: "/tools", label: "Tools", icon: Wrench },
  { href: "/resources", label: "Resources", icon: Library },
  { href: "/pricing", label: "Pricing", icon: CreditCard },
];

export default function Nav() {
  const pathname = usePathname();
  const { status } = useSession();
  const authenticated = status === "authenticated";
  const [mobileOpen, setMobileOpen] = useState(false);
  const profile = useQuery({
    queryKey: ["nav-profile", pathname],
    queryFn: () => apiGet<ProfileSummary>("/auth/profile"),
    enabled: authenticated,
  });
  const features = useQuery({
    queryKey: ["feature-decisions"],
    queryFn: () => apiGet<FeatureResponse>("/v1/features"),
    enabled: authenticated,
    staleTime: 60_000,
  });

  useEffect(() => {
    localStorage.removeItem("access_token");
  }, [pathname]);

  const links = authenticated
    ? appLinks.filter((link) => !link.feature || features.data?.features[link.feature]?.enabled !== false)
    : publicLinks;
  const units = profile.data?.ai_credits;
  const premium = profile.data?.tier === "premium";

  return (
    <header className="fixed inset-x-0 top-0 z-50 h-16 border-b border-white/10 bg-[#0f1211]/95 backdrop-blur-md">
      <div className="page-container flex h-full items-center justify-between gap-4">
        <Link href={authenticated ? "/dashboard" : "/"} className="flex shrink-0 items-center gap-2.5" aria-label="HireWiz home">
          <Logo />
          <span className="text-base font-black text-[#f4f2ea]">HireWiz</span>
        </Link>

        <nav className="hidden h-full min-w-0 items-stretch lg:flex" aria-label="Primary navigation">
          {links.map((link) => {
            const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`relative flex min-w-0 items-center gap-1.5 px-3 text-sm font-semibold transition-colors ${
                  active ? "text-white" : "text-neutral-400 hover:text-neutral-100"
                }`}
              >
                <link.icon size={15} aria-hidden="true" />
                <span>{link.label}</span>
                {active ? <span className="absolute inset-x-3 bottom-0 h-0.5 bg-primary" /> : null}
              </Link>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          {authenticated && typeof units === "number" ? (
            <Link
              href="/billing"
              className="hidden min-h-9 items-center gap-2 rounded-md border border-white/10 px-3 text-xs font-bold text-neutral-300 hover:border-white/20 hover:bg-white/5 sm:flex"
              title={premium ? "Premium is active" : `${units} analysis units remaining`}
            >
              <CircleGauge size={14} className="text-primary" aria-hidden="true" />
              <span>{premium ? "Premium" : units}</span>
            </Link>
          ) : null}

          {authenticated ? (
            <Link href="/logout" className="icon-button hidden lg:inline-flex" aria-label="Log out" title="Log out">
              <LogOut size={17} />
            </Link>
          ) : (
            <div className="hidden items-center gap-2 lg:flex">
              <Link href="/login" className="button-ghost">Log in</Link>
              <Link href="/register" className="button-primary">Create account</Link>
            </div>
          )}

          <button
            type="button"
            className="icon-button lg:hidden"
            aria-label={mobileOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen((value) => !value)}
          >
            {mobileOpen ? <X size={19} /> : <Menu size={19} />}
          </button>
        </div>
      </div>

      {mobileOpen ? (
        <div className="absolute inset-x-0 top-16 max-h-[calc(100vh-4rem)] overflow-y-auto border-b border-white/10 bg-[#111412] p-4 shadow-2xl lg:hidden">
          <nav className="page-container grid gap-1 p-0" aria-label="Mobile navigation">
            {links.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setMobileOpen(false)}
                  className={`flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-semibold ${
                    active ? "bg-primary/12 text-primary" : "text-neutral-300 hover:bg-white/5"
                  }`}
                >
                  <link.icon size={18} aria-hidden="true" />
                  {link.label}
                </Link>
              );
            })}
            <div className="mt-3 border-t border-white/10 pt-3">
              {authenticated ? (
                <Link href="/logout" onClick={() => setMobileOpen(false)} className="flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-semibold text-neutral-300 hover:bg-white/5">
                  <LogOut size={18} aria-hidden="true" /> Log out
                </Link>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  <Link href="/login" onClick={() => setMobileOpen(false)} className="button-secondary">Log in</Link>
                  <Link href="/register" onClick={() => setMobileOpen(false)} className="button-primary">Create account</Link>
                </div>
              )}
            </div>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
