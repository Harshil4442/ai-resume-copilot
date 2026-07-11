import Link from "next/link";
import { FileText, Target, TrendingUp, BookOpen, PenTool, BarChart3, CheckCircle2, Shield, Zap } from "lucide-react";
import ShimmerBadge from "../components/ui/ShimmerBadge";
import GradientHeading from "../components/ui/GradientHeading";
import AnimatedButton from "../components/ui/AnimatedButton";
import FadeIn from "../components/ui/FadeIn";
import StaggerContainer, { StaggerItem } from "../components/ui/StaggerContainer";
import Marquee from "../components/ui/Marquee";
import GlassCard from "../components/ui/GlassCard";
import BulletOptimizer from "../components/BulletOptimizer";
import ScrollReveal from "../components/ui/ScrollReveal";
import FloatingElement from "../components/ui/FloatingElement";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI-assisted resume analysis you review and control",
  description:
    "Compare your own resume with job-description text and review informational suggestions for wording, skills, market signals, and learning topics.",
  alternates: { canonical: "/" },
};

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-between pb-20">
      
      {/* Hero Section */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8 pt-24 pb-16 md:pt-32 md:pb-24 text-center">
        <StaggerContainer staggerDelay={0.15}>
          <StaggerItem className="flex justify-center mb-6">
            <ShimmerBadge href="/about">Self-service resume analysis</ShimmerBadge>
          </StaggerItem>
          
          <StaggerItem>
            <GradientHeading className="mb-6 max-w-4xl mx-auto text-5xl md:text-6xl lg:text-7xl">
              Review your resume<br className="hidden sm:block" /> against the role you choose.
            </GradientHeading>
          </StaggerItem>
          
          <StaggerItem>
            <p className="text-lg md:text-xl text-slate-400 text-balance tracking-tight max-w-2xl mx-auto leading-relaxed mb-10">
              Upload your own resume, compare it with job-description text, and review AI-assisted wording, skill-gap, market, and learning suggestions. You decide what is accurate and what to use.
            </p>
          </StaggerItem>
          
          <StaggerItem className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <AnimatedButton href="/register" showArrow>Create a free account</AnimatedButton>
            <AnimatedButton href="/pricing" variant="secondary">View public pricing</AnimatedButton>
          </StaggerItem>
        </StaggerContainer>
      </section>

      {/* Capability Banner (feature-oriented, no unverified testimonials) */}
      <div className="w-full max-w-[1000px] mx-auto py-8 mb-32 overflow-hidden flex flex-col gap-4 relative [mask-image:linear-gradient(to_right,transparent,black_15%,black_85%,transparent)]">

        {/* LTR Marquee */}
        <Marquee className="[--duration:60s]" reverse>
          <div className="flex items-center gap-4 px-2">
            {[
              { title: "Bullet clarity", text: "Rewrite resume bullets to be clearer and evidence-based." },
              { title: "Compatibility estimate", text: "Compare your resume with a job description using a HireWiz estimate." },
              { title: "Skill signals", text: "See which skills appear most in the job-posting sample you analyze." },
              { title: "Gap analysis", text: "Identify skills mentioned in a role that your resume doesn't cover yet." },
              { title: "Learning suggestions", text: "Get study topics and project ideas mapped to your skill gaps." },
            ].map((item, i) => (
              <div key={i} className="w-[340px] p-5 rounded-2xl bg-slate-900/50 border border-slate-800/80 flex flex-col gap-3 hover:bg-slate-800/50 transition">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-blue-500/20 text-blue-400 flex items-center justify-center">
                    <CheckCircle2 size={16} />
                  </div>
                  <span className="text-sm font-bold text-slate-200">{item.title}</span>
                </div>
                <p className="text-sm text-slate-400 leading-relaxed">{item.text}</p>
              </div>
            ))}
          </div>
        </Marquee>

        {/* RTL Marquee */}
        <Marquee className="[--duration:70s]">
          <div className="flex items-center gap-4 px-2">
            {[
              { title: "Structured parsing", text: "Turn your uploaded PDF into structured, reviewable sections." },
              { title: "Market snapshot", text: "Review in-demand skills from a sample of recent job postings." },
              { title: "Tailoring help", text: "Draft tailored wording that you review before saving or exporting." },
              { title: "Portfolio direction", text: "Plan projects that demonstrate the skills a target role asks for." },
              { title: "You stay in control", text: "AI suggestions are informational and require your review." },
            ].map((item, i) => (
              <div key={i} className="w-[340px] p-5 rounded-2xl bg-slate-900/50 border border-slate-800/80 flex flex-col gap-3 hover:bg-slate-800/50 transition">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-purple-500/20 text-purple-400 flex items-center justify-center">
                    <CheckCircle2 size={16} />
                  </div>
                  <span className="text-sm font-bold text-slate-200">{item.title}</span>
                </div>
                <p className="text-sm text-slate-400 leading-relaxed">{item.text}</p>
              </div>
            ))}
          </div>
        </Marquee>
      </div>

      {/* Marquee Icons Section */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8 mb-32">
        <ScrollReveal delay={0.2} direction="up">
          <div className="relative rounded-2xl bg-slate-900/40 border border-slate-700/50 backdrop-blur-md p-6 shadow-[0_0_50px_rgba(59,130,246,0.1)]">
            {/* Fade overlays */}
            <div className="pointer-events-none absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-background to-transparent z-10 rounded-l-2xl"></div>
            <div className="pointer-events-none absolute inset-y-0 right-0 w-1/3 bg-gradient-to-l from-background to-transparent z-10 rounded-r-2xl"></div>
            
            <Marquee pauseOnHover={true}>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-950 border border-slate-800 shadow-sm">
                <FileText className="text-blue-500" size={18} />
                <span className="text-sm font-semibold text-slate-200">Structure resume text</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-950 border border-slate-800 shadow-sm">
                <Target className="text-amber-500" size={18} />
                <span className="text-sm font-semibold text-slate-200">Match job descriptions</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-950 border border-slate-800 shadow-sm">
                <TrendingUp className="text-emerald-500" size={18} />
                <span className="text-sm font-semibold text-slate-200">Analyze market trends</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-950 border border-slate-800 shadow-sm">
                <BookOpen className="text-purple-500" size={18} />
                <span className="text-sm font-semibold text-slate-200">AI learning strategies</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-950 border border-slate-800 shadow-sm">
                <PenTool className="text-rose-500" size={18} />
                <span className="text-sm font-semibold text-slate-200">Draft wording for your review</span>
              </div>
            </Marquee>
          </div>
        </ScrollReveal>
      </section>

      {/* Bullet Optimizer Preview */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8 mb-32">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          <div>
            <ScrollReveal direction="left">
              <h2 className="text-3xl md:text-4xl font-bold tracking-tight text-white mb-4">
                Write clearer, evidence-based bullets.
              </h2>
              <p className="text-lg text-slate-400 mb-8 leading-relaxed">
                Our AI reviews your resume bullets and suggests stronger action verbs and places to add quantifiable metrics. Suggestions are informational and require your review.
              </p>
              <ul className="space-y-4 mb-8">
                <li className="flex items-start gap-3">
                  <CheckCircle2 className="text-emerald-500 mt-1" size={20} />
                  <span className="text-slate-200 font-medium">Identify weak action verbs for review</span>
                </li>
                <li className="flex items-start gap-3">
                  <CheckCircle2 className="text-emerald-500 mt-1" size={20} />
                  <span className="text-slate-200 font-medium">Identify missing quantifiable metrics</span>
                </li>
                <li className="flex items-start gap-3">
                  <CheckCircle2 className="text-emerald-500 mt-1" size={20} />
                  <span className="text-slate-200 font-medium">Review AI-assisted wording alternatives</span>
                </li>
              </ul>
              <AnimatedButton href="/register" variant="outline" showArrow>Try the full tool</AnimatedButton>
            </ScrollReveal>
          </div>
          <ScrollReveal direction="right" delay={0.2}>
            <FloatingElement yOffset={10} duration={6}>
              <div className="absolute -inset-8 bg-blue-500/10 rounded-full blur-3xl -z-10"></div>
              <div className="relative p-[1px] rounded-3xl bg-gradient-to-br from-blue-400/40 via-slate-800/10 to-purple-400/40 shadow-[0_0_40px_rgba(59,130,246,0.1)]">
                <div className="bg-[#020617]/80 backdrop-blur-2xl rounded-[23px] overflow-hidden">
                  <BulletOptimizer />
                </div>
              </div>
            </FloatingElement>
          </ScrollReveal>
        </div>
      </section>

      {/* Bento Grid Features */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8 mb-32">
        <ScrollReveal className="text-center mb-16">
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-white mb-4">Tools to help you present your experience.</h2>
          <p className="text-lg text-slate-400">Self-service software for the modern job seeker.</p>
        </ScrollReveal>

        <StaggerContainer className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          <StaggerItem className="lg:col-span-2">
            <GlassCard className="h-full flex flex-col p-8">
              <div className="h-12 w-12 rounded-xl bg-blue-100 flex items-center justify-center text-blue-600 mb-6">
                <Target size={24} />
              </div>
              <h3 className="text-2xl font-bold text-white mb-3">Job Description Comparison</h3>
              <p className="text-slate-400 leading-relaxed mb-6 flex-grow">
                Paste any job description and let our AI compare it against your parsed resume. See a HireWiz compatibility estimate, likely missing skills, and a breakdown of how your resume aligns with the role's requirements.
              </p>
              <Link href="/about" className="text-sm font-bold text-primary hover:text-blue-400 flex items-center gap-1 group">
                See How It Works <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Link>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="h-full flex flex-col p-8">
              <div className="h-12 w-12 rounded-xl bg-amber-100 flex items-center justify-center text-amber-600 mb-6">
                <TrendingUp size={24} />
              </div>
              <h3 className="text-xl font-bold text-white mb-3">Market Trends</h3>
              <p className="text-slate-400 leading-relaxed mb-6 flex-grow text-sm">
                Analyze a sample of recent job postings from third-party job-data providers to see which skills are frequently requested for your target role. Results are informational estimates.
              </p>
              <Link href="/about" className="text-sm font-bold text-primary hover:text-blue-400 flex items-center gap-1 group">
                Read the Limitations <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Link>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="h-full flex flex-col p-8">
              <div className="h-12 w-12 rounded-xl bg-purple-100 flex items-center justify-center text-purple-600 mb-6">
                <BookOpen size={24} />
              </div>
              <h3 className="text-xl font-bold text-white mb-3">Learning Strategies</h3>
              <p className="text-slate-400 leading-relaxed mb-6 flex-grow text-sm">
                Review study topics and project ideas based on estimated gaps between your resume and a selected job description. Suggestions may be inaccurate and require your judgment.
              </p>
              <Link href="/about" className="text-sm font-bold text-primary hover:text-blue-400 flex items-center gap-1 group">
                Learn About Suggestions <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Link>
            </GlassCard>
          </StaggerItem>

          <StaggerItem className="lg:col-span-2">
            <GlassCard className="h-full flex flex-col p-8 relative overflow-hidden group">
              <div className="absolute right-0 top-0 w-64 h-64 bg-emerald-100/50 rounded-full blur-3xl -mr-20 -mt-20 transition-transform group-hover:scale-110"></div>
              <div className="relative z-10">
                <div className="h-12 w-12 rounded-xl bg-emerald-100 flex items-center justify-center text-emerald-400 mb-6">
                  <Shield size={24} />
                </div>
                <h3 className="text-2xl font-bold text-white mb-3">Structured Resume Parsing</h3>
                <p className="text-slate-400 leading-relaxed mb-6 max-w-xl">
                  Upload your PDF and our extraction engine structures your experience into clear, reviewable sections so you can check how your resume reads. HireWiz does not produce an official ATS score.
                </p>
                <Link href="/register" className="text-sm font-bold text-primary hover:text-blue-400 flex items-center gap-1 group/link">
                  Create an Account <ArrowRight className="h-4 w-4 transition-transform group-hover/link:translate-x-1" />
                </Link>
              </div>
            </GlassCard>
          </StaggerItem>
        </StaggerContainer>
      </section>

      {/* CTA Section */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8">
        <ScrollReveal direction="up">
          <div className="relative rounded-3xl overflow-hidden bg-slate-950 p-10 md:p-20 text-center border border-slate-800 shadow-2xl">
            <div className="absolute inset-0 bg-[linear-gradient(to_right,#4f4f4f2e_1px,transparent_1px),linear-gradient(to_bottom,#4f4f4f2e_1px,transparent_1px)] bg-[size:24px_24px] [mask-image:radial-gradient(ellipse_60%_50%_at_50%_50%,#000_70%,transparent_100%)]"></div>
            
            <div className="relative z-10 max-w-2xl mx-auto">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-slate-950/10 border border-white/10 text-white text-xs font-semibold mb-6">
                <Zap size={14} className="text-amber-400" /> Start free today
              </div>
              <h2 className="text-3xl md:text-5xl font-black tracking-tight text-white mb-6">
                Ready to review your resume?
              </h2>
              <p className="text-lg text-slate-400 mb-10 text-balance">
                Create a free account and use AI-assisted tools to review, structure, and improve your resume — you stay in control of every change.
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                <AnimatedButton href="/register" showArrow>Create free account</AnimatedButton>
              </div>
            </div>
          </div>
        </ScrollReveal>
      </section>
      
    </main>
  );
}

function ArrowRight({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  );
}
