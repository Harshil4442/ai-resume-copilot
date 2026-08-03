"use client";

import { AlertCircle, BriefcaseBusiness, CheckCircle2, Crown, Download, Link as LinkIcon, Save, ShieldOff, Trash2, User } from "lucide-react";
import { signOut } from "next-auth/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import CareerMemoryPanel from "../../components/CareerMemoryPanel";
import { Button } from "../../components/ui/Button";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { resetAnalyticsIdentity, trackEvent } from "../../lib/analytics";
import { apiDownload, apiGet, apiPostJson, apiPutJson } from "../../lib/api";
import type { UserProfile } from "../../lib/types";

type ProfileForm = Omit<UserProfile, "email" | "profile_completeness" | "missing_fields" | "skills" | "tier" | "ai_credits"> & { skills_text: string };

const emptyForm: ProfileForm = {
  full_name: "", headline: "", phone: "", location: "", linkedin: "", github: "", portfolio: "",
  target_role: "", preferred_job_type: "", preferred_location: "", years_experience: 0, bio: "",
  skills_text: "", education: "", certifications: "",
};

function toForm(profile: UserProfile): ProfileForm {
  return {
    full_name: profile.full_name || "", headline: profile.headline || "", phone: profile.phone || "",
    location: profile.location || "", linkedin: profile.linkedin || "", github: profile.github || "",
    portfolio: profile.portfolio || "", target_role: profile.target_role || "",
    preferred_job_type: profile.preferred_job_type || "", preferred_location: profile.preferred_location || "",
    years_experience: profile.years_experience ?? 0, bio: profile.bio || "",
    skills_text: (profile.skills || []).join(", "), education: profile.education || "",
    certifications: profile.certifications || "",
  };
}

function splitSkills(text: string) {
  return text.split(/[,|\n]/).map((skill) => skill.trim()).filter(Boolean);
}

export default function ProfilePage() {
  const router = useRouter();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [accountBusy, setAccountBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmEndPremium, setConfirmEndPremium] = useState(false);

  useEffect(() => {
    apiGet<UserProfile>("/auth/profile")
      .then((response) => { setProfile(response); setForm(toForm(response)); })
      .catch((loadError) => setError(loadError instanceof Error ? loadError.message : "Failed to load profile"))
      .finally(() => setLoading(false));
  }, []);

  const skills = useMemo(() => splitSkills(form.skills_text), [form.skills_text]);
  function update<K extends keyof ProfileForm>(key: K, value: ProfileForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const { skills_text, ...values } = form;
      const response = await apiPutJson<UserProfile>("/auth/profile", { ...values, skills: splitSkills(skills_text), years_experience: Number(values.years_experience || 0) });
      setProfile(response);
      setForm(toForm(response));
      setSaved(true);
      trackEvent("profile_saved", { completeness: response.profile_completeness });
      window.setTimeout(() => setSaved(false), 3000);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Failed to save profile");
    } finally {
      setSaving(false);
    }
  }

  async function endPremium() {
    setAccountBusy(true);
    setError(null);
    try {
      await apiPostJson("/billing/end-premium", {});
      const response = await apiGet<UserProfile>("/auth/profile");
      setProfile(response);
      setConfirmEndPremium(false);
      trackEvent("premium_access_ended");
      window.dispatchEvent(new Event("refresh_analysis_units"));
    } catch (accountError) {
      setError(accountError instanceof Error ? accountError.message : "Could not end Premium access.");
    } finally {
      setAccountBusy(false);
    }
  }

  async function deleteAccount() {
    setAccountBusy(true);
    setError(null);
    try {
      await apiPostJson("/auth/delete-account", {});
      trackEvent("account_deleted");
      resetAnalyticsIdentity();
      await signOut({ redirect: false });
      router.push("/register");
    } catch (accountError) {
      setError(accountError instanceof Error ? accountError.message : "Could not delete account.");
      setAccountBusy(false);
    }
  }

  async function exportAccount() {
    setAccountBusy(true);
    setError(null);
    try {
      await apiDownload("/auth/export-account", "hirewiz-account-export.json");
      trackEvent("account_exported");
    } catch (accountError) {
      setError(accountError instanceof Error ? accountError.message : "Could not export account data.");
    } finally {
      setAccountBusy(false);
    }
  }

  if (loading) return <main className="app-page"><div className="page-container"><LoadingBlock rows={7} /></div></main>;

  return (
    <main className="app-page">
      <div className="page-container space-y-10">
        <header className="grid gap-6 border-b border-white/10 pb-7 lg:grid-cols-[1fr_auto] lg:items-end">
          <div><p className="eyebrow">Profile and preferences</p><h1 className="mt-2 text-3xl font-black text-neutral-100 sm:text-4xl">Your career context</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-500">Only the information visible here and in Career Memory is reusable across your workspace.</p></div>
          {profile ? <div className="min-w-56"><div className="flex items-end justify-between"><span className="data-label">Profile completeness</span><span className="text-2xl font-black text-primary">{profile.profile_completeness}%</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/8"><div className="h-full bg-primary" style={{ width: `${profile.profile_completeness}%` }} /></div><p className="mt-2 truncate text-xs text-neutral-600">{profile.email}</p></div> : null}
        </header>

        {error ? <div className="flex gap-3 border border-coral/30 bg-coral/5 p-4 text-sm text-[#ffab9e]" role="alert"><AlertCircle size={18} className="shrink-0" /> {error}</div> : null}

        <form onSubmit={save} className="space-y-6">
          <section className="surface p-5 sm:p-7">
            <div className="flex items-center gap-3"><span className="icon-tile"><User size={18} /></span><h2 className="text-lg font-black">Identity</h2></div>
            <div className="mt-6 grid gap-5 md:grid-cols-2">
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Full name<input className="field-control" value={form.full_name || ""} onChange={(event) => update("full_name", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Professional headline<input className="field-control" value={form.headline || ""} onChange={(event) => update("headline", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Phone<input className="field-control" value={form.phone || ""} onChange={(event) => update("phone", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Location<input className="field-control" value={form.location || ""} onChange={(event) => update("location", event.target.value)} /></label>
            </div>
          </section>

          <section className="surface p-5 sm:p-7">
            <div className="flex items-center gap-3"><span className="icon-tile"><LinkIcon size={18} /></span><h2 className="text-lg font-black">Professional links</h2></div>
            <div className="mt-6 grid gap-5 md:grid-cols-3">
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">LinkedIn<input className="field-control" inputMode="url" value={form.linkedin || ""} onChange={(event) => update("linkedin", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">GitHub<input className="field-control" inputMode="url" value={form.github || ""} onChange={(event) => update("github", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Portfolio<input className="field-control" inputMode="url" value={form.portfolio || ""} onChange={(event) => update("portfolio", event.target.value)} /></label>
            </div>
          </section>

          <section className="surface p-5 sm:p-7">
            <div className="flex items-center gap-3"><span className="icon-tile"><BriefcaseBusiness size={18} /></span><h2 className="text-lg font-black">Career direction</h2></div>
            <div className="mt-6 grid gap-5 md:grid-cols-2">
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Target role<input className="field-control" value={form.target_role || ""} onChange={(event) => update("target_role", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Preferred work mode<input className="field-control" value={form.preferred_job_type || ""} onChange={(event) => update("preferred_job_type", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Preferred location<input className="field-control" value={form.preferred_location || ""} onChange={(event) => update("preferred_location", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Years of experience<input className="field-control" type="number" min="0" step="0.5" value={form.years_experience ?? 0} onChange={(event) => update("years_experience", Number(event.target.value))} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300 md:col-span-2">Career summary<textarea className="field-control min-h-28 resize-y" value={form.bio || ""} onChange={(event) => update("bio", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300 md:col-span-2">Skills<textarea className="field-control min-h-24 resize-y" value={form.skills_text} onChange={(event) => update("skills_text", event.target.value)} placeholder="Python, PostgreSQL, product strategy" />{skills.length ? <span className="text-xs text-neutral-600">{skills.length} normalized skills</span> : null}</label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Education<textarea className="field-control min-h-24 resize-y" value={form.education || ""} onChange={(event) => update("education", event.target.value)} /></label>
              <label className="grid gap-2 text-sm font-semibold text-neutral-300">Certifications<textarea className="field-control min-h-24 resize-y" value={form.certifications || ""} onChange={(event) => update("certifications", event.target.value)} /></label>
            </div>
          </section>

          <div className="sticky bottom-4 z-20 flex justify-end"><div className="flex w-full items-center justify-between gap-4 rounded-lg border border-white/15 bg-[#111412]/95 p-3 shadow-xl backdrop-blur sm:w-auto"><span className={`text-sm font-bold ${saved ? "text-primary" : "text-neutral-600"}`}>{saved ? <span className="flex items-center gap-2"><CheckCircle2 size={16} /> Saved</span> : "Unsaved edits stay on this page"}</span><Button type="submit" disabled={saving}><Save size={16} /> {saving ? "Saving..." : "Save profile"}</Button></div></div>
        </form>

        <CareerMemoryPanel />

        <section className="border-t border-white/10 pt-8">
          <p className="eyebrow">Account controls</p>
          <div className="mt-5 divide-y divide-white/10 border-y border-white/10">
            <div className="grid gap-4 py-5 sm:grid-cols-[1fr_auto] sm:items-center"><div><h2 className="font-black text-neutral-200">Export account data</h2><p className="mt-1 text-sm text-neutral-500">Download your profile, career records, usage history, and payment references.</p></div><Button variant="secondary" onClick={exportAccount} disabled={accountBusy}><Download size={16} /> Export JSON</Button></div>
            <div className="grid gap-4 py-5 sm:grid-cols-[1fr_auto] sm:items-center"><div><h2 className="flex items-center gap-2 font-black text-neutral-200"><Crown size={17} className="text-accent" /> Premium access</h2><p className="mt-1 text-sm text-neutral-500">{profile?.tier === "premium" ? (profile.premium_until ? `Active until ${new Date(profile.premium_until).toLocaleDateString("en-IN")}. No automatic renewal.` : "Premium access is active.") : "Free access is active."}</p></div>{profile?.tier === "premium" ? (!confirmEndPremium ? <Button variant="secondary" onClick={() => setConfirmEndPremium(true)}><ShieldOff size={16} /> End access</Button> : <div className="flex gap-2"><Button variant="danger" onClick={endPremium} disabled={accountBusy}>Confirm</Button><Button variant="ghost" onClick={() => setConfirmEndPremium(false)}>Cancel</Button></div>) : <Button asChild><Link href="/billing">View Premium</Link></Button>}</div>
            <div className="grid gap-4 py-5 sm:grid-cols-[1fr_auto] sm:items-center"><div><h2 className="font-black text-[#ffab9e]">Delete account</h2><p className="mt-1 max-w-2xl text-sm text-neutral-500">Delete career data and unlink retained accounting records. This cannot be undone.</p></div>{!confirmDelete ? <Button variant="danger" onClick={() => setConfirmDelete(true)}><Trash2 size={16} /> Delete account</Button> : <div className="flex flex-wrap gap-2"><Button variant="danger" onClick={deleteAccount} disabled={accountBusy}>{accountBusy ? "Deleting..." : "Permanently delete"}</Button><Button variant="ghost" onClick={() => setConfirmDelete(false)} disabled={accountBusy}>Cancel</Button></div>}</div>
          </div>
        </section>
      </div>
    </main>
  );
}
