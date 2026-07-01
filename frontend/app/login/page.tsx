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

            <div className="relative flex items-center py-2">
              <div className="flex-grow border-t border-slate-200"></div>
              <span className="flex-shrink-0 mx-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">or</span>
              <div className="flex-grow border-t border-slate-200"></div>
            </div>

            <button type="button" onClick={() => signIn("google", { callbackUrl: "/dashboard" })} className="w-full bg-white border border-slate-200 hover:bg-slate-50 hover:border-slate-300 text-slate-700 font-bold py-3 px-4 rounded-xl flex items-center justify-center gap-3 transition">
              <svg viewBox="0 0 24 24" className="w-5 h-5"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
              Sign in with Google
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
