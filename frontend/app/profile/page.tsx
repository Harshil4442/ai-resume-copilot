"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPutJson } from "../../lib/api";
import type { UserProfile } from "../../lib/types";

type ProfileForm = Omit<UserProfile, "email" | "profile_completeness" | "missing_fields" | "skills"> & {
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
    } catch (e: any) {
      setError(e?.message || "Failed to save profile");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="max-w-4xl mx-auto py-10 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Profile</h1>
        <p className="text-sm text-gray-500 mt-1">
          Add optional career details so the app has better context for your resume, matches, and dashboard.
        </p>
      </div>

      {loading && <div className="text-sm text-gray-600">Loading...</div>}
      {error && <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}
      {ok && <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-4 py-3">Profile saved.</div>}

      {profile && (
        <div className="border border-gray-200 rounded-xl p-5 bg-white shadow-sm">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div>
              <div className="text-xs text-gray-500">Signed in as</div>
              <div className="text-sm font-semibold text-gray-900">{profile.email}</div>
            </div>
            <div className="text-right">
              <div className="text-xs text-gray-500">Profile completeness</div>
              <div className="text-2xl font-bold text-blue-700">{profile.profile_completeness}%</div>
            </div>
          </div>
          {profile.missing_fields.length > 0 && (
            <div className="mt-4">
              <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Optional fields to improve</div>
              <div className="flex flex-wrap gap-2">
                {profile.missing_fields.slice(0, 8).map((field) => (
                  <span key={field} className="px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-100 text-xs">
                    {field}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {!loading && (
        <form onSubmit={save} className="border border-gray-200 rounded-xl p-6 bg-white shadow-sm space-y-6">
          <section>
            <h2 className="text-sm font-bold text-gray-900 mb-3">Basic Info</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Full name" value={form.full_name || ""} onChange={(e) => update("full_name", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Professional headline" value={form.headline || ""} onChange={(e) => update("headline", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Phone" value={form.phone || ""} onChange={(e) => update("phone", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Location" value={form.location || ""} onChange={(e) => update("location", e.target.value)} />
            </div>
          </section>

          <section>
            <h2 className="text-sm font-bold text-gray-900 mb-3">Links</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="LinkedIn URL" value={form.linkedin || ""} onChange={(e) => update("linkedin", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="GitHub URL" value={form.github || ""} onChange={(e) => update("github", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Portfolio URL" value={form.portfolio || ""} onChange={(e) => update("portfolio", e.target.value)} />
            </div>
          </section>

          <section>
            <h2 className="text-sm font-bold text-gray-900 mb-3">Career Preferences</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Target role" value={form.target_role || ""} onChange={(e) => update("target_role", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Preferred job type, e.g. Remote, Hybrid" value={form.preferred_job_type || ""} onChange={(e) => update("preferred_job_type", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Preferred location" value={form.preferred_location || ""} onChange={(e) => update("preferred_location", e.target.value)} />
              <input className="border rounded-lg px-3 py-2 text-sm" placeholder="Years of experience" type="number" min="0" step="0.5" value={form.years_experience ?? 0} onChange={(e) => update("years_experience", Number(e.target.value))} />
            </div>
          </section>

          <section>
            <h2 className="text-sm font-bold text-gray-900 mb-3">Profile Details</h2>
            <div className="space-y-4">
              <textarea className="w-full border rounded-lg px-3 py-2 text-sm min-h-[110px]" placeholder="Short bio or career summary" value={form.bio || ""} onChange={(e) => update("bio", e.target.value)} />
              <textarea className="w-full border rounded-lg px-3 py-2 text-sm min-h-[90px]" placeholder="Skills, separated by commas or new lines" value={form.skills_text} onChange={(e) => update("skills_text", e.target.value)} />
              <textarea className="w-full border rounded-lg px-3 py-2 text-sm min-h-[90px]" placeholder="Education" value={form.education || ""} onChange={(e) => update("education", e.target.value)} />
              <textarea className="w-full border rounded-lg px-3 py-2 text-sm min-h-[90px]" placeholder="Certifications" value={form.certifications || ""} onChange={(e) => update("certifications", e.target.value)} />
            </div>
          </section>

          {skills.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {skills.map((skill) => (
                <span key={skill} className="px-2 py-1 rounded-full bg-gray-100 text-gray-700 text-xs">
                  {skill}
                </span>
              ))}
            </div>
          )}

          <button disabled={saving} className="px-4 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition disabled:opacity-50">
            {saving ? "Saving..." : "Save Profile"}
          </button>
        </form>
      )}
    </main>
  );
}
