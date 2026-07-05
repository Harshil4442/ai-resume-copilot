"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPutJson } from "../../lib/api";
import type { UserProfile } from "../../lib/types";
import PageHeader from "../../components/ui/PageHeader";
import GlassCard from "../../components/ui/GlassCard";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import { User, Link as LinkIcon, Briefcase, FileText, CheckCircle2, AlertTriangle, AlertCircle, Save } from "lucide-react";

type ProfileForm = Omit<UserProfile, "email" | "profile_completeness" | "missing_fields" | "skills" | "tier" | "ai_credits"> & {
  skills_text: string;
};

const emptyForm: ProfileForm = {
  full_name: "",
  headline: "",
  phone: "",
  location: "",
  linkedin: "",
  github: "",
  portfolio: "",
  target_role: "",
  preferred_job_type: "",
  preferred_location: "",
  years_experience: 0,
  bio: "",
  skills_text: "",
  education: "",
  certifications: "",
};

function toForm(profile: UserProfile): ProfileForm {
  return {
    full_name: profile.full_name || "",
    headline: profile.headline || "",
    phone: profile.phone || "",
    location: profile.location || "",
    linkedin: profile.linkedin || "",
    github: profile.github || "",
    portfolio: profile.portfolio || "",
    target_role: profile.target_role || "",
    preferred_job_type: profile.preferred_job_type || "",
    preferred_location: profile.preferred_location || "",
    years_experience: profile.years_experience ?? 0,
    bio: profile.bio || "",
    skills_text: (profile.skills || []).join(", "),
    education: profile.education || "",
    certifications: profile.certifications || "",
  };
}

function splitSkills(text: string) {
  return text
    .split(/[,|\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function ProfilePage() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  useEffect(() => {
    apiGet<UserProfile>("/auth/profile")
      .then((data) => {
        setProfile(data);
        setForm(toForm(data));
      })
      .catch((e: any) => setError(e?.message || "Failed to load profile"))
      .finally(() => setLoading(false));
  }, []);

  const skills = useMemo(() => splitSkills(form.skills_text), [form.skills_text]);

  function update<K extends keyof ProfileForm>(key: K, value: ProfileForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setOk(false);
    try {
      const payload = {
        ...form,
        skills,
        years_experience: Number(form.years_experience || 0),
      };
      delete (payload as any).skills_text;
      const saved = await apiPutJson<UserProfile>("/auth/profile", payload);
      setProfile(saved);
      setForm(toForm(saved));
      setOk(true);
      setTimeout(() => setOk(false), 3000);
    } catch (e: any) {
      setError(e?.message || "Failed to save profile");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <main className="w-full max-w-4xl mx-auto px-4 py-16 flex justify-center">
        <div className="w-12 h-12 rounded-full border-4 border-slate-200 border-t-primary animate-spin"></div>
      </main>
    );
  }

  return (
    <main className="w-full max-w-[64rem] mx-auto px-4 sm:px-6 md:px-8 py-8 space-y-10">
      <PageHeader 
        badge="Identity Layer"
        title="Give the AI better career context."
        subtitle="Add optional career details so the app has better context for your resume, matches, and dashboard."
      />

      {profile && (
        <FadeIn>
          <GlassCard className="p-6 md:p-8 flex flex-col md:flex-row justify-between gap-6 bg-gradient-to-r from-slate-900 to-blue-950 border-slate-800 text-white" hoverEffect={false}>
            <div className="flex items-center gap-5">
              <div className="w-16 h-16 rounded-2xl bg-white/10 border border-white/20 flex items-center justify-center backdrop-blur-sm text-2xl font-black shadow-inner">
                {profile.email.charAt(0).toUpperCase()}
              </div>
              <div>
                <div className="text-[10px] font-bold text-blue-300 uppercase tracking-widest mb-1">Signed In</div>
                <div className="text-lg font-black tracking-tight">{profile.email}</div>
              </div>
            </div>

            <div className="flex items-center gap-6">
              <div className="hidden md:block w-px h-12 bg-white/10"></div>
              <div>
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">Completeness</div>
                <div className="flex items-center gap-3">
                  <div className="text-3xl font-black">{profile.profile_completeness}%</div>
                  <div className="w-24 h-2 rounded-full bg-white/10 overflow-hidden">
                    <div 
                      className="h-full bg-blue-500 rounded-full" 
                      style={{ width: `${profile.profile_completeness}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>
          </GlassCard>
          
          {profile.missing_fields.length > 0 && (
            <div className="mt-4 px-2">
              <div className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-2 flex items-center gap-1.5"><AlertTriangle size={12} className="text-amber-500" /> Missing Context Fields</div>
              <div className="flex flex-wrap gap-2">
                {profile.missing_fields.slice(0, 8).map((field) => (
                  <span key={field} className="px-2.5 py-1 rounded-md bg-amber-50 border border-amber-100 text-[10px] font-bold text-amber-700 shadow-sm uppercase tracking-wider">
                    {field.replace('_', ' ')}
                  </span>
                ))}
              </div>
            </div>
          )}
        </FadeIn>
      )}

      {error && (
        <FadeIn>
          <div className="flex items-center gap-2 text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-xl px-4 py-3 shadow-sm">
            <AlertCircle size={16} /> {error}
          </div>
        </FadeIn>
      )}

      <form onSubmit={save} className="space-y-6">
        <StaggerContainer className="space-y-6">
          <StaggerItem>
            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center"><User size={20} /></div>
                <h2 className="text-xl font-black text-slate-900 tracking-tighter">Basic Info</h2>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Full Name</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="John Doe" value={form.full_name || ""} onChange={(e) => update("full_name", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Professional Headline</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="Senior Software Engineer at ACME" value={form.headline || ""} onChange={(e) => update("headline", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Phone</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="+1 555-0000" value={form.phone || ""} onChange={(e) => update("phone", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Location</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="San Francisco, CA" value={form.location || ""} onChange={(e) => update("location", e.target.value)} />
                </div>
              </div>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-purple-50 text-purple-600 flex items-center justify-center"><LinkIcon size={20} /></div>
                <h2 className="text-xl font-black text-slate-900 tracking-tighter">Links</h2>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">LinkedIn</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="https://linkedin.com/in/..." value={form.linkedin || ""} onChange={(e) => update("linkedin", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">GitHub</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="https://github.com/..." value={form.github || ""} onChange={(e) => update("github", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Portfolio</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="https://yourwebsite.com" value={form.portfolio || ""} onChange={(e) => update("portfolio", e.target.value)} />
                </div>
              </div>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center"><Briefcase size={20} /></div>
                <h2 className="text-xl font-black text-slate-900 tracking-tighter">Career Preferences</h2>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Target Role</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="Backend Engineer" value={form.target_role || ""} onChange={(e) => update("target_role", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Preferred Job Type</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="Remote, Hybrid" value={form.preferred_job_type || ""} onChange={(e) => update("preferred_job_type", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Preferred Location</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="US, UK" value={form.preferred_location || ""} onChange={(e) => update("preferred_location", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Years of Experience</label>
                  <input className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10" placeholder="0" type="number" min="0" step="0.5" value={form.years_experience ?? 0} onChange={(e) => update("years_experience", Number(e.target.value))} />
                </div>
              </div>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="p-6 md:p-8" hoverEffect={false}>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-orange-50 text-orange-600 flex items-center justify-center"><FileText size={20} /></div>
                <h2 className="text-xl font-black text-slate-900 tracking-tighter">Details & Education</h2>
              </div>
              
              <div className="space-y-5">
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Short Bio</label>
                  <textarea className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 min-h-[100px] resize-y" placeholder="Brief career summary..." value={form.bio || ""} onChange={(e) => update("bio", e.target.value)} />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Manual Skills</label>
                  <textarea className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 min-h-[80px] resize-y" placeholder="React, Python, AWS (comma separated)" value={form.skills_text} onChange={(e) => update("skills_text", e.target.value)} />
                  
                  {skills.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-3 p-3 bg-slate-50 rounded-xl border border-slate-100">
                      {skills.map((skill) => (
                        <span key={skill} className="px-2.5 py-1 rounded-md bg-white border border-slate-200 text-[10px] font-bold text-slate-700 shadow-sm uppercase tracking-wider">
                          {skill}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <div>
                    <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Education</label>
                    <textarea className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 min-h-[80px] resize-y" placeholder="BSc Computer Science..." value={form.education || ""} onChange={(e) => update("education", e.target.value)} />
                  </div>
                  <div>
                    <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-700 mb-1.5 px-1">Certifications</label>
                    <textarea className="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 min-h-[80px] resize-y" placeholder="AWS Certified Solutions Architect..." value={form.certifications || ""} onChange={(e) => update("certifications", e.target.value)} />
                  </div>
                </div>
              </div>
            </GlassCard>
          </StaggerItem>
        </StaggerContainer>

        <div className="sticky bottom-6 z-20 flex justify-end">
          <div className="flex items-center gap-4 bg-white/80 backdrop-blur-xl p-4 rounded-2xl shadow-xl border border-slate-200 w-full md:w-auto">
            {ok && (
              <FadeIn className="hidden md:flex items-center gap-2 text-emerald-600 text-sm font-bold bg-emerald-50 px-4 py-2 rounded-xl">
                <CheckCircle2 size={16} /> Saved
              </FadeIn>
            )}
            <AnimatedButton disabled={saving} className="w-full md:w-48 py-3" showArrow={false}>
              {saving ? "Saving..." : <><Save size={18} className="mr-2 inline" /> Save Profile</>}
            </AnimatedButton>
          </div>
        </div>
      </form>
    </main>
  );
}
