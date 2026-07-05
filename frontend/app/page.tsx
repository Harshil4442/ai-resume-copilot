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

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-between pb-20">
      
      {/* Hero Section */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8 pt-24 pb-16 md:pt-32 md:pb-24 text-center">
        <StaggerContainer staggerDelay={0.15}>
          <StaggerItem className="flex justify-center mb-6">
            <ShimmerBadge href="/register">✨ Introducing AI-Powered Career OS</ShimmerBadge>
          </StaggerItem>
          
          <StaggerItem>
            <GradientHeading className="mb-6 max-w-4xl mx-auto">
              Your career signal,<br className="hidden sm:block" /> measured and amplified.
            </GradientHeading>
          </StaggerItem>
          
          <StaggerItem>
            <p className="text-lg md:text-xl text-slate-500 max-w-2xl mx-auto mb-10 text-balance tracking-tight leading-relaxed">
              Resume CoPilot offers instant, AI-powered insights into your career trajectory. Parse your resume, match against job descriptions, and unlock personalized learning strategies.
            </p>
          </StaggerItem>
          
          <StaggerItem className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <AnimatedButton href="/register" showArrow>Start your transformation</AnimatedButton>
            <AnimatedButton href="/dashboard" variant="secondary">Go to Dashboard</AnimatedButton>
          </StaggerItem>
        </StaggerContainer>
      </section>

      {/* Marquee Section */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8 mb-32">
        <ScrollReveal delay={0.2} direction="up">
          <div className="relative rounded-2xl bg-white/40 border border-slate-200/50 backdrop-blur-sm p-4 md:shadow-xl">
            {/* Fade overlays */}
            <div className="pointer-events-none absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-background to-transparent z-10 rounded-l-2xl"></div>
            <div className="pointer-events-none absolute inset-y-0 right-0 w-1/3 bg-gradient-to-l from-background to-transparent z-10 rounded-r-2xl"></div>
            
            <Marquee pauseOnHover={true}>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white border border-slate-100 shadow-sm">
                <FileText className="text-blue-500" size={18} />
                <span className="text-sm font-semibold text-slate-700">Parse resumes instantly</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white border border-slate-100 shadow-sm">
                <Target className="text-amber-500" size={18} />
                <span className="text-sm font-semibold text-slate-700">Match job descriptions</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white border border-slate-100 shadow-sm">
                <TrendingUp className="text-emerald-500" size={18} />
                <span className="text-sm font-semibold text-slate-700">Analyze market trends</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white border border-slate-100 shadow-sm">
                <BookOpen className="text-purple-500" size={18} />
                <span className="text-sm font-semibold text-slate-700">AI learning strategies</span>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white border border-slate-100 shadow-sm">
                <PenTool className="text-rose-500" size={18} />
                <span className="text-sm font-semibold text-slate-700">Tailor resumes automatically</span>
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
              <h2 className="text-3xl md:text-4xl font-bold tracking-tight text-slate-900 mb-4">
                Write bullets that actually convert.
              </h2>
              <p className="text-lg text-slate-500 mb-8 leading-relaxed">
                Our AI analyzes your resume bullets against thousands of successful tech resumes to suggest high-impact verbs and quantifiable metrics.
              </p>
              <ul className="space-y-4 mb-8">
                <li className="flex items-start gap-3">
                  <CheckCircle2 className="text-emerald-500 mt-1" size={20} />
                  <span className="text-slate-700 font-medium">Detect weak action verbs automatically</span>
                </li>
                <li className="flex items-start gap-3">
                  <CheckCircle2 className="text-emerald-500 mt-1" size={20} />
                  <span className="text-slate-700 font-medium">Identify missing quantifiable metrics</span>
                </li>
                <li className="flex items-start gap-3">
                  <CheckCircle2 className="text-emerald-500 mt-1" size={20} />
                  <span className="text-slate-700 font-medium">Generate rewritten alternatives instantly</span>
                </li>
              </ul>
              <AnimatedButton href="/register" variant="outline" showArrow>Try the full tool</AnimatedButton>
            </ScrollReveal>
          </div>
          <ScrollReveal direction="right" delay={0.2}>
            <FloatingElement yOffset={10} duration={6}>
              <div className="absolute -inset-4 bg-primary/5 rounded-3xl blur-2xl -z-10"></div>
              <GlassCard className="p-1 md:p-1 border-white/80 shadow-2xl">
                <div className="bg-slate-50/50 rounded-[14px] overflow-hidden">
                  <BulletOptimizer />
                </div>
              </GlassCard>
            </FloatingElement>
          </ScrollReveal>
        </div>
      </section>

      {/* Bento Grid Features */}
      <section className="w-full max-w-[80rem] mx-auto px-6 md:px-8 mb-32">
        <ScrollReveal className="text-center mb-16">
          <h2 className="text-3xl md:text-5xl font-bold tracking-tight text-slate-900 mb-4">Everything you need to land the job.</h2>
          <p className="text-lg text-slate-500">Powerful tools designed for the modern job seeker.</p>
        </ScrollReveal>

        <StaggerContainer className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          <StaggerItem className="lg:col-span-2">
            <GlassCard className="h-full flex flex-col p-8">
              <div className="h-12 w-12 rounded-xl bg-blue-100 flex items-center justify-center text-blue-600 mb-6">
                <Target size={24} />
              </div>
              <h3 className="text-2xl font-bold text-slate-900 mb-3">Precision Job Matching</h3>
              <p className="text-slate-500 leading-relaxed mb-6 flex-grow">
                Paste any job description and let our AI compare it against your parsed resume. Instantly see your match score, missing skills, and a breakdown of how a recruiter views your profile.
              </p>
              <Link href="/jobs" className="text-sm font-bold text-primary hover:text-blue-700 flex items-center gap-1 group">
                Explore Job Match <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Link>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="h-full flex flex-col p-8">
              <div className="h-12 w-12 rounded-xl bg-amber-100 flex items-center justify-center text-amber-600 mb-6">
                <TrendingUp size={24} />
              </div>
              <h3 className="text-xl font-bold text-slate-900 mb-3">Market Trends</h3>
              <p className="text-slate-500 leading-relaxed mb-6 flex-grow text-sm">
                Analyze thousands of recent job postings for your target role to discover which skills are actually in demand right now.
              </p>
              <Link href="/market" className="text-sm font-bold text-primary hover:text-blue-700 flex items-center gap-1 group">
                View Trends <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Link>
            </GlassCard>
          </StaggerItem>

          <StaggerItem>
            <GlassCard className="h-full flex flex-col p-8">
              <div className="h-12 w-12 rounded-xl bg-purple-100 flex items-center justify-center text-purple-600 mb-6">
                <BookOpen size={24} />
              </div>
              <h3 className="text-xl font-bold text-slate-900 mb-3">Learning Strategies</h3>
              <p className="text-slate-500 leading-relaxed mb-6 flex-grow text-sm">
                Get a personalized study plan based on your exact skill gaps. We recommend specific projects to build that prove you know what you're doing.
              </p>
              <Link href="/learning" className="text-sm font-bold text-primary hover:text-blue-700 flex items-center gap-1 group">
                Plan Strategy <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Link>
            </GlassCard>
          </StaggerItem>

          <StaggerItem className="lg:col-span-2">
            <GlassCard className="h-full flex flex-col p-8 relative overflow-hidden group">
              <div className="absolute right-0 top-0 w-64 h-64 bg-emerald-100/50 rounded-full blur-3xl -mr-20 -mt-20 transition-transform group-hover:scale-110"></div>
              <div className="relative z-10">
                <div className="h-12 w-12 rounded-xl bg-emerald-100 flex items-center justify-center text-emerald-600 mb-6">
                  <Shield size={24} />
                </div>
                <h3 className="text-2xl font-bold text-slate-900 mb-3">Enterprise-Grade Parsing</h3>
                <p className="text-slate-500 leading-relaxed mb-6 max-w-xl">
                  Upload your PDF and our extraction engine structures your experience precisely how an ATS sees it. Verify that your formatting isn't costing you interviews.
                </p>
                <Link href="/resume" className="text-sm font-bold text-primary hover:text-blue-700 flex items-center gap-1 group/link">
                  Parse Resume <ArrowRight className="h-4 w-4 transition-transform group-hover/link:translate-x-1" />
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
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/10 border border-white/10 text-white text-xs font-semibold mb-6">
                <Zap size={14} className="text-amber-400" /> Start free today
              </div>
              <h2 className="text-3xl md:text-5xl font-black tracking-tight text-white mb-6">
                Ready to level up your career?
              </h2>
              <p className="text-lg text-slate-400 mb-10 text-balance">
                Join thousands of engineers who are using AI to build better resumes and land their dream roles.
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
