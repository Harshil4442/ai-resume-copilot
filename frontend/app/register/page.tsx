"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { prepareGoogleRegistrationConsent, register } from "../../lib/auth";
import { getProviders, signIn } from "next-auth/react";
import GlassCard from "../../components/ui/GlassCard";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import { Sparkles, FileText, Target, Zap } from "lucide-react";

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(false);
  const [googleAvailable, setGoogleAvailable] = useState(false);

  useEffect(() => {
    getProviders()
      .then((providers) => setGoogleAvailable(Boolean(providers?.google)))
      .catch(() => setGoogleAvailable(false));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!agreed) {
      setError("Please agree to the Terms of Service and Privacy Policy to continue.");
      return;
    }
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
    <main className="w-full min-h-[calc(100vh-80px)] flex items-center justify-center p-4 md:p-8">
      <div className="w-full max-w-5xl grid grid-cols-1 lg:grid-cols-[450px_1fr] gap-6 lg:gap-8 items-stretch">
        
        <FadeIn>
          <GlassCard className="p-8 md:p-10 h-full flex flex-col justify-center bg-slate-900/70 backdrop-blur-xl border-white" hoverEffect={false}>
            <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4">Create Profile</div>
            <h2 className="text-3xl font-black text-white tracking-tighter mb-2">Create your HireWiz account</h2>
            <p className="text-sm text-slate-400 font-medium mb-8">Upload your own resume, compare it with job-description text, and review every AI-assisted suggestion before use.</p>

            <form onSubmit={onSubmit} className="space-y-4">
              <div className="space-y-4">
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-200 mb-1.5 px-1">Email Address</label>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3.5 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 font-medium" placeholder="you@example.com" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-200 mb-1.5 px-1">Password</label>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3.5 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 font-medium" placeholder="At least 10 characters" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={10} maxLength={128} required autoComplete="new-password" />
                </div>
              </div>

              <label className="flex items-start gap-2.5 pt-1 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-600 bg-slate-900/50 accent-primary cursor-pointer"
                />
                <span className="text-[11px] leading-relaxed text-slate-400">
                  I am at least 18 years old and I agree to the{" "}
                  <Link href="/terms" className="text-slate-300 hover:text-primary underline">Terms of Service</Link>{" "}
                  and acknowledge the{" "}
                  <Link href="/privacy" className="text-slate-300 hover:text-primary underline">Privacy Policy</Link>.
                </span>
              </label>

              <div className="pt-2">
                <AnimatedButton type="submit" disabled={loading || !agreed} className="w-full py-3.5 text-base shadow-md" showArrow>
                  {loading ? "Creating account..." : "Create account"}
                </AnimatedButton>
              </div>
            </form>

            {googleAvailable ? (
              <>
            <div className="relative flex items-center py-6">
              <div className="flex-grow border-t border-slate-700"></div>
              <span className="flex-shrink-0 mx-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">Or Continue With</span>
              <div className="flex-grow border-t border-slate-700"></div>
            </div>

            <button
              type="button"
              onClick={async () => {
                if (!agreed) {
                  setError("Please agree to the Terms of Service and Privacy Policy to continue.");
                  return;
                }
                setError(null);
                setLoading(true);
                try {
                  await prepareGoogleRegistrationConsent();
                  await signIn("google", { callbackUrl: "/dashboard" });
                } catch (err: any) {
                  setError(err?.message || "Could not start Google sign-in.");
                  setLoading(false);
                }
              }}
              disabled={!agreed || loading}
              className="w-full bg-slate-950 border border-slate-700 hover:bg-slate-900/50 hover:border-slate-600 text-slate-200 font-bold py-3.5 px-4 rounded-xl flex items-center justify-center gap-3 transition-all shadow-sm active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
              Google
            </button>
              </>
            ) : null}

            {ok && <div className="mt-6 text-sm text-emerald-400 font-bold bg-emerald-900/30 border border-emerald-800 rounded-xl px-4 py-3 text-center">Account created successfully! <Link className="underline decoration-2" href="/login">Log in now</Link></div>}
            {error && <div className="mt-6 text-sm text-rose-400 font-bold bg-rose-900/30 border border-rose-800 rounded-xl px-4 py-3">{error}</div>}

            <div className="mt-auto pt-8 text-center text-sm text-slate-400 font-medium">
              Already have an account? <Link className="font-bold text-primary hover:underline transition-all" href="/login">Log in</Link>
            </div>
          </GlassCard>
        </FadeIn>

        <GlassCard className="hidden lg:flex flex-col justify-between p-10 bg-gradient-to-br from-slate-900 to-blue-950 border-slate-800 text-white overflow-hidden relative" hoverEffect={false}>
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,_var(--tw-gradient-stops))] from-primary/30 via-transparent to-transparent pointer-events-none"></div>
          
          <div className="relative z-10">
            <div className="text-[10px] font-black text-blue-400 uppercase tracking-widest mb-6 flex items-center gap-2">
              <Sparkles size={14} /> Self-service software
            </div>
            <h1 className="text-5xl font-black text-white tracking-tighter mb-6 leading-[1.1]">
              Your resume.<br/>Your review and control.
            </h1>
          </div>

          <StaggerContainer className="relative z-10 grid grid-cols-1 gap-4 mt-12">
            {[
              { title: "Resume Parsing", desc: "Structure text from a resume you are authorized to use", icon: FileText },
              { title: "Market Analysis", desc: "Review an on-demand sample from configured job-data providers", icon: Target },
              { title: "Learning Suggestions", desc: "Review project ideas based on estimated skill gaps", icon: Zap },
            ].map((item) => (
              <StaggerItem key={item.title}>
                <div className="bg-slate-950/5 border border-white/10 rounded-2xl p-5 backdrop-blur-sm flex items-center gap-4 hover:bg-slate-950/10 transition-colors">
                  <div className="w-12 h-12 rounded-xl bg-blue-900/300/20 flex items-center justify-center flex-shrink-0">
                    <item.icon size={20} className="text-blue-400" />
                  </div>
                  <div>
                    <div className="text-base font-black text-white mb-0.5 tracking-tight">{item.title}</div>
                    <div className="text-xs font-medium text-slate-400">{item.desc}</div>
                  </div>
                </div>
              </StaggerItem>
            ))}
          </StaggerContainer>
        </GlassCard>
      </div>
    </main>
  );
}
