"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import GlassCard from "../../components/ui/GlassCard";
import AnimatedButton from "../../components/ui/AnimatedButton";
import FadeIn from "../../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../../components/ui/StaggerContainer";
import { Sparkles, ArrowRight, Code, LayoutDashboard } from "lucide-react";
import ScaleIn from "../../components/ui/ScaleIn";
import Link from "next/link";

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
    <main className="w-full min-h-[calc(100vh-80px)] flex items-center justify-center p-4 md:p-8">
      <div className="w-full max-w-5xl grid grid-cols-1 lg:grid-cols-[1fr_450px] gap-6 lg:gap-8 items-stretch">
        <ScaleIn delay={0.1} className="h-full">
          <GlassCard className="hidden lg:flex flex-col justify-between p-10 bg-slate-900 border-slate-800 text-white overflow-hidden relative h-full" hoverEffect={false}>
            <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_bottom_right,_var(--tw-gradient-stops))] from-primary/20 via-slate-900 to-slate-900 pointer-events-none"></div>
          
          <div className="relative z-10">
            <div className="text-[10px] font-black text-blue-400 uppercase tracking-widest mb-6 flex items-center gap-2">
              <Sparkles size={14} /> Welcome Back
            </div>
            <h1 className="text-5xl font-black text-white tracking-tighter mb-6 leading-[1.1]">
              Your career command center is waiting.
            </h1>
            <p className="text-slate-400 font-medium leading-relaxed max-w-md">
              Continue analyzing roles, tracking skill signals, and converting gaps into project evidence with AI.
            </p>
          </div>

          <StaggerContainer className="relative z-10 grid grid-cols-3 gap-4 mt-12">
            {[
              { title: "Parse", desc: "Resume signals", icon: Code },
              { title: "Match", desc: "Job alignment", icon: LayoutDashboard },
              { title: "Improve", desc: "Action plan", icon: Sparkles }
            ].map((item) => (
              <StaggerItem key={item.title}>
                <div className="bg-slate-950/5 border border-white/10 rounded-2xl p-4 backdrop-blur-sm h-full hover:bg-slate-950/10 transition-colors">
                  <item.icon size={18} className="text-blue-400 mb-3" />
                  <div className="text-sm font-black text-white mb-1">{item.title}</div>
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{item.desc}</div>
                </div>
              </StaggerItem>
            ))}
          </StaggerContainer>
          </GlassCard>
        </ScaleIn>

        <FadeIn>
          <GlassCard className="p-8 md:p-10 h-full flex flex-col justify-center bg-slate-900/70 backdrop-blur-xl border-white" hoverEffect={false}>
            <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-4">Sign In</div>
            <h2 className="text-3xl font-black text-white tracking-tighter mb-2">Enter workspace</h2>
            <p className="text-sm text-slate-400 font-medium mb-8">Use your account to access resumes, matches, strategies, and market trends.</p>

            <form onSubmit={onSubmit} className="space-y-4">
              <div className="space-y-4">
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-200 mb-1.5 px-1">Email Address</label>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3.5 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 font-medium placeholder:text-slate-300/80" placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
                </div>
                <div>
                  <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-200 mb-1.5 px-1">Password</label>
                  <input className="w-full rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3.5 text-sm outline-none transition focus:border-primary focus:ring-4 focus:ring-primary/10 font-medium placeholder:text-slate-300/80" placeholder="••••••••" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
                </div>
              </div>

              <div className="pt-2">
                <AnimatedButton disabled={loading} className="w-full py-3.5 text-base shadow-md" showArrow>
                  {loading ? "Signing in..." : "Sign in to account"}
                </AnimatedButton>
              </div>
            </form>

            <div className="relative flex items-center py-6">
              <div className="flex-grow border-t border-slate-700"></div>
              <span className="flex-shrink-0 mx-4 text-[10px] font-black text-slate-400 uppercase tracking-widest">Or Continue With</span>
              <div className="flex-grow border-t border-slate-700"></div>
            </div>

            <button 
              type="button" 
              onClick={() => signIn("google", { callbackUrl: "/dashboard" })} 
              className="w-full bg-slate-950 border border-slate-700 hover:bg-slate-900/50 hover:border-slate-600 text-slate-200 font-bold py-3.5 px-4 rounded-xl flex items-center justify-center gap-3 transition-all shadow-sm active:scale-[0.98]"
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
              Google
            </button>

            {error && <div className="mt-6 text-sm text-rose-400 font-bold bg-rose-900/30 border border-rose-800 rounded-xl px-4 py-3">{error}</div>}

            <div className="mt-auto pt-8 text-center text-sm text-slate-400 font-medium">
              New to AI Resume CoPilot? <Link className="font-bold text-primary hover:underline transition-all" href="/register">Create an account</Link>
            </div>
          </GlassCard>
        </FadeIn>
      </div>
    </main>
  );
}
