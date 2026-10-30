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
import { trackEvent } from "../../lib/analytics";
import { unifiedCatalogOptions } from "../../lib/unifiedCreditClient";
import { ownedDashboardOptions } from "../../lib/ownedDashboardClient";
import { stageLabels, stageTone } from "../../lib/career";
import type { UserProfile } from "../../lib/types";


function firstName(profile: UserProfile | undefined) {
  return profile?.full_name?.trim().split(/\s+/)[0] || "there";
}

function formatDue(value: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

export default function DashboardPage() {
  const snapshot = useQuery(ownedDashboardOptions());
  // A refused current snapshot must not keep rendering a prior successful read.
  const data = snapshot.isError ? undefined : snapshot.data;
  const profile = { data: data?.bootstrap.profile ?? undefined };
  const analytics = { data: data?.summary ?? undefined };
  const opportunities = { data: data?.opportunities ?? undefined };
  const reminders = { data: data?.reminders ?? undefined };
  const features = data?.bootstrap.features;
  const workspaceEnabled = features?.career_workspace?.enabled === true;
  const creditAvailability = data?.bootstrap.credit_availability;
  const wallet = data?.bootstrap.credit_wallet;
  const unifiedWalletEnabled = creditAvailability?.catalog_enabled === true;
  const shouldOfferUpgrade = unifiedWalletEnabled && wallet !== null && wallet !== undefined && wallet.available_credits <= 10;
  const billingCatalog = useQuery({ ...unifiedCatalogOptions(), enabled: shouldOfferUpgrade });
  const upgradeTracked = useRef(false);
  const loading = snapshot.isLoading;
  const partial = Boolean(data && (data.unavailable.length || data.bootstrap.unavailable.length));
  const active = (opportunities.data?.items || []).filter((item) => !["rejected", "withdrawn", "archived"].includes(item.stage));
  const interviews = active.filter((item) => item.stage === "interviewing").length;
  const activation = [
    { label: "Add your first resume", complete: analytics.data ? analytics.data.resume_count > 0 : null, href: "/resume" },
    ...(workspaceEnabled ? [
      { label: "Create a target opportunity", complete: opportunities.data ? opportunities.data.total > 0 : null, href: "/workspace?new=1" },
      { label: "Run an evidence-aware match", complete: analytics.data ? analytics.data.applications_count > 0 : null, href: "/workspace" },
    ] : []),
    { label: "Complete your career profile", complete: profile.data ? profile.data.profile_completeness >= 70 : null, href: "/profile" },
  ];
  const progressKnown = features !== null && features !== undefined && activation.every((item) => item.complete !== null);
  const activated = activation.filter((item) => item.complete).length;
  const nextActivation = activation.find((item) => item.complete === false);
  const upgradeProduct = shouldOfferUpgrade && billingCatalog.data?.checkout_enabled === true
    ? billingCatalog.data.products.find((product) => product.purchase_enabled)
    : undefined;

  useEffect(() => {
    if (!upgradeProduct || upgradeTracked.current) return;
    upgradeTracked.current = true;
    trackEvent("upgrade_prompt_viewed", { surface: "dashboard_low_credits", reason: "low_credits", sku: upgradeProduct.sku });
  }, [upgradeProduct]);

  return (
    <main className="app-page">
      <div className="page-container">
        <header className="grid gap-6 border-b border-border pb-7 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <p className="eyebrow">Today</p>
            <h1 className="font-display mt-2 text-4xl font-normal text-foreground sm:text-5xl">Good to see you, {firstName(profile.data)}.</h1>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">Keep the next application moving without losing its context.</p>
          </div>
          <Button asChild><Link href={workspaceEnabled ? "/workspace?new=1" : "/resume"}>{workspaceEnabled ? <Plus size={16} /> : <FileText size={16} />} {workspaceEnabled ? "Add opportunity" : "Add resume"}</Link></Button>
        </header>

        {loading ? <div className="mt-8"><LoadingBlock rows={6} /></div> : null}
        {!loading && (snapshot.isError || partial) ? (
          <div className="mt-8 border-y border-coral/25 bg-coral/5 px-5 py-5 text-sm text-coral">Some dashboard data could not be loaded. Your saved workspace is unchanged. <Button size="sm" variant="ghost" disabled={snapshot.isFetching} onClick={() => void snapshot.refetch()}>Refresh dashboard</Button></div>
        ) : null}

        {!loading ? (
          <>
            <section className="grid border-b border-border sm:grid-cols-2 xl:grid-cols-4" aria-label="Career search summary">
              {[
                { label: "Active roles", value: opportunities.data ? active.length : "Unavailable", icon: BriefcaseBusiness, tone: "text-primary" },
                { label: "Interviews", value: opportunities.data ? interviews : "Unavailable", icon: Target, tone: "text-primary" },
                { label: "Average match", value: analytics.data ? analytics.data.applications_count ? Math.round(analytics.data.average_match_score) : "-" : "Unavailable", icon: Gauge, tone: "text-foreground" },
                { label: unifiedWalletEnabled ? "Available credits" : "Analysis units", value: unifiedWalletEnabled ? wallet ? wallet.available_credits : "Unavailable" : profile.data?.ai_credits ?? "Unavailable", icon: Circle, tone: "text-coral" },
              ].map((metric) => (
                <div key={metric.label} className="border-t border-border py-6 sm:border-r sm:px-6 sm:first:pl-0 xl:border-t-0 last:border-r-0">
                  <div className="flex items-center justify-between"><p className="data-label">{metric.label}</p><metric.icon size={16} className={metric.tone} /></div>
                  <p className="mt-3 text-3xl font-semibold text-foreground">{metric.value}</p>
                </div>
              ))}
            </section>

            <div className="mt-10 grid gap-12 xl:grid-cols-[1fr_360px]">
              <section className="min-w-0">
                {workspaceEnabled ? (
                  <>
                <div className="flex items-end justify-between gap-4">
                  <div><p className="eyebrow">Priority queue</p><h2 className="font-display mt-2 text-2xl font-normal">Move these forward</h2></div>
                  <Link href="/workspace" className="text-sm font-bold text-muted-foreground hover:text-primary">All opportunities</Link>
                </div>
                {active.length ? (
                  <div className="mt-5 border-t border-border">
                    {active.slice(0, 5).map((item) => (
                      <Link key={item.id} href={`/workspace/${item.id}`} className="group grid gap-3 border-b border-border py-5 transition-colors hover:bg-surface sm:grid-cols-[1fr_auto] sm:items-center">
                        <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-foreground">{item.title}</h3><StatusBadge tone={stageTone[item.stage]}>{stageLabels[item.stage]}</StatusBadge></div><p className="mt-1 text-sm text-muted-foreground">{item.company || "Company not set"}{item.next_action ? ` · ${item.next_action}` : ""}</p></div>
                        <ArrowRight size={17} className="hidden text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-primary sm:block" />
                      </Link>
                    ))}
                  </div>
                ) : !opportunities.data ? <p className="mt-5 text-sm" role="status">Opportunities are temporarily unavailable.</p> : (
                  <EmptyState icon={BriefcaseBusiness} title="No active opportunities" description="Add a target role to turn your resume, evidence, learning, and follow-up into one workflow." action={<Button asChild><Link href="/workspace?new=1"><Plus size={16} /> Add opportunity</Link></Button>} />
                )}

                <div className="mt-12">
                  <div className="flex items-end justify-between gap-4"><div><p className="eyebrow">Upcoming</p><h2 className="font-display mt-2 text-2xl font-normal">Reminders</h2></div></div>
                  {(reminders.data || []).length ? (
                    <div className="mt-5 divide-y divide-border border-y border-border">
                      {(reminders.data || []).slice(0, 5).map((reminder) => (
                        <div key={reminder.id} className="grid gap-2 py-4 sm:grid-cols-[1fr_auto] sm:items-center"><div className="flex min-w-0 items-start gap-3"><Clock3 size={16} className="mt-0.5 shrink-0 text-primary" /><p className="text-sm font-semibold text-foreground">{reminder.message}</p></div><p className="pl-7 text-xs font-bold text-muted-foreground sm:pl-0">{formatDue(reminder.due_at)}</p></div>
                      ))}
                    </div>
                  ) : <p className="mt-5 border-y border-border py-6 text-sm text-muted-foreground">{reminders.data ? "No scheduled follow-ups." : "Reminders are temporarily unavailable."}</p>}
                </div>
                  </>
                ) : (
                  <EmptyState icon={FileText} title="Build your resume evidence" description={features ? "Career Workspace is not enabled for this account yet. Your resume and profile tools remain available." : "Workspace availability could not be loaded. Refresh the dashboard to check again."} action={<Button asChild><Link href="/resume">Add resume</Link></Button>} />
                )}
              </section>

              <aside className="space-y-9">
                <section className="surface-panel p-5">
                  <div className="flex items-center justify-between"><div><p className="data-label">Activation</p><h2 className="font-display mt-1 text-lg font-normal">{progressKnown ? `${activated} of ${activation.length} complete` : "Progress unavailable"}</h2></div><span className="text-2xl font-semibold text-primary">{progressKnown ? `${Math.round((activated / activation.length) * 100)}%` : "—"}</span></div>
                  <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface"><div className="h-full bg-primary" style={{ width: progressKnown ? `${(activated / activation.length) * 100}%` : "0%" }} /></div>
                  <ol className="mt-5 space-y-1">
                    {activation.map((step) => (
                      <li key={step.label}><Link href={step.href} className="flex min-h-10 items-center gap-3 rounded-md px-2 text-sm text-muted-foreground hover:bg-surface hover:text-foreground">{step.complete ? <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground"><Check size={13} /></span> : <Circle size={19} className="text-muted-foreground" />}<span className={step.complete ? "text-muted-foreground line-through" : ""}>{step.label}{step.complete === null ? " · Unavailable" : ""}</span></Link></li>
                    ))}
                  </ol>
                  {nextActivation ? <Button asChild className="mt-5 w-full" variant="secondary"><Link href={nextActivation.href}>Continue setup <ArrowRight size={15} /></Link></Button> : null}
                </section>

                {upgradeProduct ? (
                  <section className="border-l-2 border-primary/30 pl-5">
                    <p className="data-label">Low credit balance</p>
                    <h2 className="font-display mt-2 text-lg font-normal text-foreground">{upgradeProduct.name}</h2>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      Review current pass prices, credit quantities and expiry before purchasing. New passes last 30 days and do not renew automatically. Older purchases keep their original terms.
                    </p>
                    <Button asChild className="mt-4" size="sm"><Link href="/billing" onClick={() => trackEvent("upgrade_prompt_clicked", { surface: "dashboard_low_credits", reason: "low_credits", sku: upgradeProduct.sku })}>Review credit passes <ArrowRight size={14} /></Link></Button>
                  </section>
                ) : null}

                <section className="border-t border-border pt-7">
                  <p className="data-label">Resume signal</p>
                  <div className="mt-4 grid grid-cols-2 gap-5"><div><p className="text-2xl font-semibold">{analytics.data ? analytics.data.resume_quality.evidenced_skills : "Unavailable"}</p><p className="mt-1 text-xs text-muted-foreground">Evidenced skills</p></div><div><p className="text-2xl font-semibold">{analytics.data ? `${analytics.data.resume_quality.verification_rate}%` : "Unavailable"}</p><p className="mt-1 text-xs text-muted-foreground">Verification</p></div></div>
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
