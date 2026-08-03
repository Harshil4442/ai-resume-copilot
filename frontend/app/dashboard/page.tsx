"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BriefcaseBusiness,
  Check,
  Circle,
  Clock3,
  FileText,
  Gauge,
  Plus,
  Target,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";

import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { apiGet } from "../../lib/api";
import { trackEvent } from "../../lib/analytics";
import type { OpportunityList, Reminder } from "../../lib/career";
import { stageLabels, stageTone } from "../../lib/career";
import type { AnalyticsSummary, UserProfile } from "../../lib/types";

type FeatureResponse = { features: Record<string, { enabled: boolean }> };
type BillingCatalog = {
  checkout_enabled: boolean;
  products: { sku: string; name: string; amount_minor: number; currency: string; entitlement_quantity: number }[];
};

function firstName(profile: UserProfile | undefined) {
  return profile?.full_name?.trim().split(/\s+/)[0] || "there";
}

function formatDue(value: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

export default function DashboardPage() {
  const profile = useQuery({ queryKey: ["profile"], queryFn: () => apiGet<UserProfile>("/auth/profile") });
  const analytics = useQuery({ queryKey: ["analytics-summary"], queryFn: () => apiGet<AnalyticsSummary>("/analytics/summary") });
  const features = useQuery({ queryKey: ["features"], queryFn: () => apiGet<FeatureResponse>("/v1/features") });
  const upgradeTracked = useRef(false);
  const workspaceEnabled = features.data?.features.career_workspace?.enabled !== false;
  const opportunities = useQuery({ queryKey: ["opportunities"], queryFn: () => apiGet<OpportunityList>("/v1/opportunities?limit=8"), enabled: features.isSuccess && workspaceEnabled });
  const reminders = useQuery({ queryKey: ["reminders", "scheduled"], queryFn: () => apiGet<Reminder[]>("/v1/reminders?status=scheduled"), enabled: features.isSuccess && workspaceEnabled });
  const shouldOfferUpgrade = profile.data?.tier === "free" && (profile.data?.ai_credits ?? 50) <= 10;
  const billingCatalog = useQuery({ queryKey: ["billing-catalog"], queryFn: () => apiGet<BillingCatalog>("/public/billing/catalog"), enabled: shouldOfferUpgrade });

  const loading = profile.isLoading || analytics.isLoading || features.isLoading || (workspaceEnabled && opportunities.isLoading);
  const active = (opportunities.data?.items || []).filter((item) => !["rejected", "withdrawn", "archived"].includes(item.stage));
  const interviews = active.filter((item) => item.stage === "interviewing").length;
  const activation = [
    { label: "Add your first resume", complete: (analytics.data?.resume_count || 0) > 0, href: "/resume" },
    ...(workspaceEnabled ? [
      { label: "Create a target opportunity", complete: (opportunities.data?.total || 0) > 0, href: "/workspace?new=1" },
      { label: "Run an evidence-aware match", complete: (analytics.data?.applications_count || 0) > 0, href: "/workspace" },
    ] : []),
    { label: "Complete your career profile", complete: (profile.data?.profile_completeness || 0) >= 70, href: "/profile" },
  ];
  const activated = activation.filter((item) => item.complete).length;
  const nextActivation = activation.find((item) => !item.complete);
  const upgradeProduct = billingCatalog.data?.checkout_enabled ? billingCatalog.data.products[0] : undefined;

  useEffect(() => {
    if (!upgradeProduct || upgradeTracked.current) return;
    upgradeTracked.current = true;
    trackEvent("upgrade_prompt_viewed", { surface: "dashboard_low_units", reason: "low_units", sku: upgradeProduct.sku });
  }, [upgradeProduct]);

  return (
    <main className="app-page">
      <div className="page-container">
        <header className="grid gap-6 border-b border-white/10 pb-7 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <p className="eyebrow">Today</p>
            <h1 className="mt-2 text-3xl font-black text-neutral-100 sm:text-4xl">Good to see you, {firstName(profile.data)}.</h1>
            <p className="mt-2 text-sm leading-6 text-neutral-500">Keep the next application moving without losing its context.</p>
          </div>
          <Button asChild><Link href={workspaceEnabled ? "/workspace?new=1" : "/resume"}>{workspaceEnabled ? <Plus size={16} /> : <FileText size={16} />} {workspaceEnabled ? "Add opportunity" : "Add resume"}</Link></Button>
        </header>

        {loading ? <div className="mt-8"><LoadingBlock rows={6} /></div> : null}
        {!loading && (profile.isError || analytics.isError || features.isError || (workspaceEnabled && opportunities.isError)) ? (
          <div className="mt-8 border-y border-coral/25 bg-coral/5 px-5 py-5 text-sm text-[#ffab9e]">Some dashboard data could not be loaded. Your saved workspace is unchanged.</div>
        ) : null}

        {!loading ? (
          <>
            <section className="grid border-b border-white/10 sm:grid-cols-2 xl:grid-cols-4" aria-label="Career search summary">
              {[
                { label: "Active roles", value: active.length, icon: BriefcaseBusiness, tone: "text-primary" },
                { label: "Interviews", value: interviews, icon: Target, tone: "text-accent" },
                { label: "Average match", value: analytics.data?.applications_count ? Math.round(analytics.data.average_match_score) : "-", icon: Gauge, tone: "text-[#f4f2ea]" },
                { label: "Analysis units", value: profile.data?.tier === "premium" ? "Premium" : profile.data?.ai_credits ?? 0, icon: Circle, tone: "text-coral" },
              ].map((metric) => (
                <div key={metric.label} className="border-t border-white/10 py-6 sm:border-r sm:px-6 sm:first:pl-0 xl:border-t-0 last:border-r-0">
                  <div className="flex items-center justify-between"><p className="data-label">{metric.label}</p><metric.icon size={16} className={metric.tone} /></div>
                  <p className="mt-3 text-3xl font-black text-neutral-100">{metric.value}</p>
                </div>
              ))}
            </section>

            <div className="mt-10 grid gap-12 xl:grid-cols-[1fr_360px]">
              <section className="min-w-0">
                {workspaceEnabled ? (
                  <>
                <div className="flex items-end justify-between gap-4">
                  <div><p className="eyebrow">Priority queue</p><h2 className="mt-2 text-2xl font-black">Move these forward</h2></div>
                  <Link href="/workspace" className="text-sm font-bold text-neutral-500 hover:text-primary">All opportunities</Link>
                </div>
                {active.length ? (
                  <div className="mt-5 border-t border-white/10">
                    {active.slice(0, 5).map((item) => (
                      <Link key={item.id} href={`/workspace/${item.id}`} className="group grid gap-3 border-b border-white/10 py-5 sm:grid-cols-[1fr_auto] sm:items-center">
                        <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-black text-neutral-200">{item.title}</h3><StatusBadge tone={stageTone[item.stage]}>{stageLabels[item.stage]}</StatusBadge></div><p className="mt-1 text-sm text-neutral-500">{item.company || "Company not set"}{item.next_action ? ` · ${item.next_action}` : ""}</p></div>
                        <ArrowRight size={17} className="hidden text-neutral-700 transition-transform group-hover:translate-x-1 group-hover:text-primary sm:block" />
                      </Link>
                    ))}
                  </div>
                ) : (
                  <EmptyState icon={BriefcaseBusiness} title="No active opportunities" description="Add a target role to turn your resume, evidence, learning, and follow-up into one workflow." action={<Button asChild><Link href="/workspace?new=1"><Plus size={16} /> Add opportunity</Link></Button>} />
                )}

                <div className="mt-12">
                  <div className="flex items-end justify-between gap-4"><div><p className="eyebrow">Upcoming</p><h2 className="mt-2 text-2xl font-black">Reminders</h2></div></div>
                  {(reminders.data || []).length ? (
                    <div className="mt-5 divide-y divide-white/10 border-y border-white/10">
                      {(reminders.data || []).slice(0, 5).map((reminder) => (
                        <div key={reminder.id} className="grid gap-2 py-4 sm:grid-cols-[1fr_auto] sm:items-center"><div className="flex min-w-0 items-start gap-3"><Clock3 size={16} className="mt-0.5 shrink-0 text-accent" /><p className="text-sm font-semibold text-neutral-300">{reminder.message}</p></div><p className="pl-7 text-xs font-bold text-neutral-600 sm:pl-0">{formatDue(reminder.due_at)}</p></div>
                      ))}
                    </div>
                  ) : <p className="mt-5 border-y border-white/10 py-6 text-sm text-neutral-600">No scheduled follow-ups.</p>}
                </div>
                  </>
                ) : (
                  <EmptyState icon={FileText} title="Build your resume evidence" description="Career Workspace is not enabled for this account yet. Your resume and profile tools remain available." action={<Button asChild><Link href="/resume">Add resume</Link></Button>} />
                )}
              </section>

              <aside className="space-y-9">
                <section className="surface-panel p-5">
                  <div className="flex items-center justify-between"><div><p className="data-label">Activation</p><h2 className="mt-1 text-lg font-black">{activated} of {activation.length} complete</h2></div><span className="text-2xl font-black text-primary">{Math.round((activated / activation.length) * 100)}%</span></div>
                  <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/8"><div className="h-full bg-primary" style={{ width: `${(activated / activation.length) * 100}%` }} /></div>
                  <ol className="mt-5 space-y-1">
                    {activation.map((step) => (
                      <li key={step.label}><Link href={step.href} className="flex min-h-10 items-center gap-3 rounded-md px-2 text-sm text-neutral-400 hover:bg-white/5 hover:text-neutral-200">{step.complete ? <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-[#07120f]"><Check size={13} /></span> : <Circle size={19} className="text-neutral-700" />}<span className={step.complete ? "text-neutral-600 line-through" : ""}>{step.label}</span></Link></li>
                    ))}
                  </ol>
                  {nextActivation ? <Button asChild className="mt-5 w-full" variant="secondary"><Link href={nextActivation.href}>Continue setup <ArrowRight size={15} /></Link></Button> : null}
                </section>

                {upgradeProduct ? (
                  <section className="border-l-2 border-accent pl-5">
                    <p className="data-label">Low analysis units</p>
                    <h2 className="mt-2 text-lg font-black text-neutral-200">{upgradeProduct.name}</h2>
                    <p className="mt-2 text-sm leading-6 text-neutral-500">
                      {new Intl.NumberFormat("en-IN", { style: "currency", currency: upgradeProduct.currency, maximumFractionDigits: 0 }).format(upgradeProduct.amount_minor / 100)} for {upgradeProduct.entitlement_quantity} days. One-time payment, no automatic renewal, with the published Premium usage policy.
                    </p>
                    <Button asChild className="mt-4" size="sm"><Link href="/billing" onClick={() => trackEvent("upgrade_prompt_clicked", { surface: "dashboard_low_units", reason: "low_units", sku: upgradeProduct.sku })}>Review Premium <ArrowRight size={14} /></Link></Button>
                  </section>
                ) : null}

                <section className="border-t border-white/10 pt-7">
                  <p className="data-label">Resume signal</p>
                  <div className="mt-4 grid grid-cols-2 gap-5"><div><p className="text-2xl font-black">{analytics.data?.resume_quality?.evidenced_skills || 0}</p><p className="mt-1 text-xs text-neutral-600">Evidenced skills</p></div><div><p className="text-2xl font-black">{analytics.data?.resume_quality?.verification_rate || 0}%</p><p className="mt-1 text-xs text-neutral-600">Verification</p></div></div>
                  <Button asChild variant="ghost" className="mt-4 px-0"><Link href="/resume"><FileText size={15} /> Review resume</Link></Button>
                </section>
              </aside>
            </div>
          </>
        ) : null}
      </div>
    </main>
  );
}
