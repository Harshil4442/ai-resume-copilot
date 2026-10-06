"use client";

export default function CookiePreferencesButton() {
  return (
    <button
      type="button"
      onClick={() => window.dispatchEvent(new Event("hirewiz:open-cookie-preferences"))}
      className="button-secondary hover:text-primary"
    >
      Open Cookie Preferences
    </button>
  );
}
