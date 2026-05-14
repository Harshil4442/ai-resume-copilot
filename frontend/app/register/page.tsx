"use client";

import Link from "next/link";
import { useState } from "react";
import { register } from "../../lib/auth";

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setOk(false);
    setLoading(true);

    try {
      await register(email, password);
      setOk(true);
    } catch (err: any) {
      setError(err?.message || "Register failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="grid grid-cols-1 lg:grid-cols-[430px_1fr] gap-6 items-stretch">
        <form onSubmit={onSubmit} className="panel kinetic-border p-6 md:p-8 flex flex-col justify-center">
          <div className="label-kicker">Create profile</div>
          <h1 className="text-4xl font-black text-slate-950 mt-2 ink-gradient">Start building your career signal.</h1>
          <p className="text-sm text-slate-500 mt-2">Create an account, upload a resume, and begin matching against the roles you want.</p>

          <div className="space-y-4 mt-8">
            <input className="field" placeholder="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input className="field" placeholder="Password (min 6 chars)" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={6} required />
            <button disabled={loading} className="btn-primary w-full">
              {loading ? "Creating..." : "Create account"}
            </button>
          </div>

          {ok && <div className="mt-4 text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-4 py-3">Account created. <Link className="font-bold underline" href="/login">Log in</Link></div>}
          {error && <div className="mt-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}

          <div className="text-sm text-slate-500 mt-6">
            Already have an account? <Link className="font-bold text-slate-950 underline" href="/login">Log in</Link>
          </div>
        </form>

        <div className="dark-panel hero-stage live-grid scanline kinetic-border p-8 md:p-10 min-h-[560px] flex flex-col justify-between">
          <div>
            <div className="label-kicker text-blue-200 flex items-center gap-3"><span className="pulse-dot" />Career OS</div>
            <h2 className="mt-5 text-5xl md:text-7xl font-black leading-[0.88] holo-text">One profile. Every career signal connected.</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {[
              ["Resume", "Parse and verify"],
              ["Market", "Demand snapshot"],
              ["Learning", "Project strategy"],
            ].map(([title, text]) => (
              <div key={title} className="rail-card tilt-lift">
                <div className="text-sm font-black text-white">{title}</div>
                <div className="text-xs text-blue-100 mt-1">{text}</div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
