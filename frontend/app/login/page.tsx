"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await signIn("credentials", {
        email,
        password,
        redirect: false,
      });
      if (res?.error) {
        setError("Invalid email or password");
      } else {
        router.push("/dashboard");
      }
    } catch (e: any) {
      setError(e.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }


  return (
    <main className="app-shell">
      <section className="grid grid-cols-1 lg:grid-cols-[1fr_430px] gap-6 items-stretch">
        <div className="dark-panel hero-stage live-grid scanline kinetic-border p-8 md:p-10 min-h-[560px] flex flex-col justify-between">
          <div>
            <div className="label-kicker text-blue-200 flex items-center gap-3"><span className="pulse-dot" />Welcome back</div>
            <h1 className="mt-5 text-5xl md:text-7xl font-black leading-[0.88] holo-text">Your career command center is waiting.</h1>
            <p className="mt-5 max-w-xl text-blue-100 leading-relaxed">
              Continue analyzing roles, tracking skill signals, and converting gaps into project evidence.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-3">
            {["Parse", "Match", "Improve"].map((item) => (
              <div key={item} className="rail-card tilt-lift">
                <div className="text-sm font-black text-white">{item}</div>
                <div className="text-[11px] text-blue-100 mt-1">career signal</div>
              </div>
            ))}
          </div>
        </div>

        <form onSubmit={onSubmit} className="panel kinetic-border p-6 md:p-8 flex flex-col justify-center">
          <div className="label-kicker">Sign in</div>
          <h2 className="text-4xl font-black text-slate-950 mt-2 ink-gradient">Enter workspace</h2>
          <p className="text-sm text-slate-500 mt-2">Use your account to access resumes, matches, strategies, and market trends.</p>

          <div className="space-y-4 mt-8">
            <input className="field" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
            <input className="field" placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button disabled={loading} className="btn-primary w-full">
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </div>

          {error && <div className="mt-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>}

          <div className="text-sm text-slate-500 mt-6">
            New here? <a className="font-bold text-slate-950 underline" href="/register">Create an account</a>
          </div>
        </form>
      </section>
    </main>
  );
}
