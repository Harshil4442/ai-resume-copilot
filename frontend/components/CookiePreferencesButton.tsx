"use client";

export default function CookiePreferencesButton() {
  return (
    <button
      type="button"
      onClick={() => window.dispatchEvent(new Event("hirewiz:open-cookie-preferences"))}
      className="inline-flex rounded-xl border border-slate-600 bg-slate-900 px-4 py-2.5 text-sm font-bold text-white hover:border-primary hover:text-primary transition-colors"
    >
      Open Cookie Preferences
    </button>
  );
}
