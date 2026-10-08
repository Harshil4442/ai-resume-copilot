"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BriefcaseBusiness,
  CircleGauge,
  CreditCard,
  FileText,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  User,
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
  job_service_credits?: number;
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
  { href: "/employer-jobs", label: "Jobs", icon: Search },
  { href: "/resume", label: "Resume", icon: FileText },
  { href: "/market", label: "Market", icon: Search },
  { href: "/profile", label: "Profile", icon: User },
  { href: "/billing", label: "Billing", icon: CreditCard },
];

const publicLinks: NavLink[] = [
  { href: "/#benefits", label: "Benefits", icon: BriefcaseBusiness },
  { href: "/#how-it-works", label: "How it works", icon: FileText },
  { href: "/pricing", label: "Pricing", icon: CreditCard },
  { href: "/contact", label: "Contact", icon: User },
];

export default function Nav() {
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const { data: session, status } = useSession();
  const authenticated = status === "authenticated";
  const [mobileOpen, setMobileOpen] = useState(false);
  const profile = useQuery({
    queryKey: ["nav-profile", session?.user?.email],
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

  useEffect(() => {
    function refreshCredits() {
      void queryClient.invalidateQueries({ queryKey: ["nav-profile"] });
      void queryClient.invalidateQueries({ queryKey: ["profile"] });
      void queryClient.invalidateQueries({ queryKey: ["employer-jobs", "catalog"] });
    }
    window.addEventListener("refresh_analysis_units", refreshCredits);
    return () => window.removeEventListener("refresh_analysis_units", refreshCredits);
  }, [queryClient]);

  const links = authenticated
    ? appLinks.filter((link) => !link.feature || features.data?.features[link.feature]?.enabled !== false)
    : publicLinks;
  const units = profile.data?.ai_credits;
  const premium = profile.data?.tier === "premium";
  const serviceCredits = profile.data?.job_service_credits;

  return (
    <header className="fixed inset-x-0 top-0 z-50 h-16 border-b border-border bg-white/95 backdrop-blur-md">
      <div className="page-container flex h-full items-center justify-between gap-4">
        <Link href={authenticated ? "/dashboard" : "/"} className="flex shrink-0 items-center gap-2.5" aria-label="HireWiz home">
          <Logo />
          <span className="font-display text-[26px] leading-none text-foreground">HireWiz</span>
        </Link>

        <nav className="hidden h-full min-w-0 items-stretch xl:flex" aria-label="Primary navigation">
          {links.map((link) => {
            const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`relative flex min-w-0 items-center gap-1.5 px-3 text-sm font-medium transition-colors ${
                  active ? "text-primary" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {authenticated ? <link.icon size={15} aria-hidden="true" /> : null}
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
              className="hidden min-h-9 items-center gap-2 rounded-full border border-border bg-surface px-3 text-xs font-medium text-foreground transition-colors hover:border-accent hover:bg-accent/10 sm:flex"
              title={premium ? "Premium is active" : `${units} analysis units remaining`}
            >
              <CircleGauge size={14} className="text-primary" aria-hidden="true" />
              <span>{premium ? "Premium" : units}</span>
            </Link>
          ) : null}

          {authenticated && typeof serviceCredits === "number" ? <Link href="/billing" className="hidden min-h-9 items-center gap-1.5 rounded-full border border-border px-3 text-xs sm:flex" title={`${serviceCredits} job service credits`}><CreditCard size={14} aria-hidden="true" /><span>{serviceCredits} service</span></Link> : null}

          {authenticated ? (
            <Link href="/logout" className="icon-button hidden xl:inline-flex" aria-label="Log out" title="Log out">
              <LogOut size={17} />
            </Link>
          ) : (
            <div className="hidden items-center gap-2 xl:flex">
              <Link href="/login" className="button-ghost">Log in</Link>
              <Link href="/register" className="button-primary">Build your workspace</Link>
            </div>
          )}

          <button
            type="button"
            className="icon-button xl:hidden"
            aria-label={mobileOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={mobileOpen}
            aria-controls={mobileOpen ? "mobile-navigation" : undefined}
            onClick={() => setMobileOpen((value) => !value)}
          >
            {mobileOpen ? <X size={19} /> : <Menu size={19} />}
          </button>
        </div>
      </div>

      {mobileOpen ? (
        <div className="absolute inset-x-0 top-16 max-h-[calc(100vh-4rem)] overflow-y-auto border-b border-border bg-white p-4 shadow-[0_16px_30px_rgba(23,23,23,0.06)] xl:hidden">
          <nav id="mobile-navigation" className="page-container grid gap-1 p-0" aria-label="Mobile navigation">
            {links.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setMobileOpen(false)}
                  className={`flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-semibold ${
                    active ? "bg-primary/8 text-primary" : "text-muted-foreground hover:bg-surface hover:text-foreground"
                  }`}
                >
                  <link.icon size={18} aria-hidden="true" />
                  {link.label}
                </Link>
              );
            })}
            <div className="mt-3 border-t border-border pt-3">
              {authenticated ? (
                <Link href="/logout" onClick={() => setMobileOpen(false)} className="flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-semibold text-muted-foreground hover:bg-surface">
                  <LogOut size={18} aria-hidden="true" /> Log out
                </Link>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  <Link href="/login" onClick={() => setMobileOpen(false)} className="button-secondary">Log in</Link>
                  <Link href="/register" onClick={() => setMobileOpen(false)} className="button-primary">Build your workspace</Link>
                </div>
              )}
            </div>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
