const features = [
  {
    href: "/resume",
    eyebrow: "Resume Intelligence",
    title: "A parser that sees evidence, not just keywords.",
    text: "Upload your resume and turn messy career history into structured skills, sections, experience, and proof signals.",
  },
  {
    href: "/jobs",
    eyebrow: "Match Analysis",
    title: "Recruiter-style fit, explained in plain English.",
    text: "Compare a resume to a target job, inspect the gaps, and ask grounded AI questions about the match.",
  },
  {
    href: "/market",
    eyebrow: "Market Trends",
    title: "Live demand, mapped back to your profile.",
    text: "Search current roles, extract repeated skill demand, and see which market gaps matter most.",
  },
  {
    href: "/learning",
    eyebrow: "Learning Strategy",
    title: "Projects that prove the missing skills.",
    text: "Turn each gap into practical build ideas, resume bullets, and interview talking points.",
  },
];

const screenRows = [
  ["Profile signal", "82%", "Improving"],
  ["Backend Engineer", "91", "Strong match"],
  ["Docker", "High", "Market gap"],
  ["RAG Assistant", "Build", "Proof project"],
];

const numberField = `01 10 94 82 17 03 59 21 88 04
resume proof skill gap market fit signal
100 011 001 111 010 101 011 100
FastAPI React Redis Groq GCP Vercel
match score verified evidence learning`;

export default function HomePage(): JSX.Element {
  return (
    <main className="app-shell space-y-6 md:space-y-8">
      <section className="product-hero">
        <div className="label-kicker">AI Resume CoPilot</div>
        <h1 className="product-title mt-5">
          A career workspace that feels as sharp as your ambition.
        </h1>
        <p className="product-subtitle">
          Parse resumes. Match jobs. Read the market. Build proof. One beautifully connected system for making better career moves.
        </p>
        <div className="mt-7 flex flex-wrap justify-center gap-3">
          <a className="btn-primary" href="/dashboard">Open Dashboard</a>
          <a className="btn-secondary" href="/market">Explore Market Trends</a>
        </div>

        <div className="product-stage">
          <div className="number-field">{numberField}</div>
          <div className="device-frame relative z-10">
            <div className="screen-glass">
              <div className="border-b border-slate-200 bg-white/80 px-4 py-3 flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
                <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                <span className="ml-auto text-[11px] font-black uppercase tracking-[0.22em] text-slate-400">Career OS</span>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] min-h-[430px]">
                <div className="p-5 md:p-7">
                  <div className="label-kicker">Live Career Signal</div>
                  <h2 className="mt-3 text-4xl md:text-6xl font-black tracking-tight leading-[0.88] text-slate-950">
                    Everything important, in focus.
                  </h2>
                  <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {screenRows.map(([name, value, state]) => (
                      <div key={name} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                        <div className="text-xs font-bold uppercase tracking-[0.16em] text-slate-500">{name}</div>
                        <div className="mt-3 text-4xl font-black text-slate-950">{value}</div>
                        <div className="mt-1 text-sm font-semibold text-[#0071e3]">{state}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="border-t lg:border-l lg:border-t-0 border-slate-200 bg-[#f5f5f7] p-5 md:p-7">
                  <div className="rounded-lg bg-neutral-950 p-4 text-white shadow-[0_22px_70px_rgba(15,23,42,0.22)]">
                    <div className="label-kicker text-blue-200">Ask AI</div>
                    <p className="mt-4 text-2xl font-black leading-tight">Why is this match not higher?</p>
                    <div className="mt-5 space-y-3 text-sm text-blue-100">
                      <div className="rounded-lg border border-white/10 bg-white/10 p-3">Evidence is strong for APIs and Python.</div>
                      <div className="rounded-lg border border-white/10 bg-white/10 p-3">Docker and cloud deployment are not proven enough.</div>
                      <div className="rounded-lg border border-white/10 bg-white/10 p-3">Build one deployment project to close the gap.</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {features.map((feature) => (
          <a key={feature.href} href={feature.href} className="premium-card p-6 md:p-8 min-h-[300px] flex flex-col justify-between">
            <div>
              <div className="label-kicker">{feature.eyebrow}</div>
              <h2 className="mt-4 text-3xl md:text-5xl font-black tracking-tight leading-[0.94] text-slate-950">
                {feature.title}
              </h2>
              <p className="mt-4 text-sm md:text-base font-medium leading-relaxed text-slate-600">{feature.text}</p>
            </div>
            <div className="mt-8 line-link">Open feature</div>
          </a>
        ))}
      </section>

      <section className="dark-panel hero-stage live-grid p-7 md:p-10 overflow-hidden">
        <div className="number-field">{numberField}</div>
        <div className="relative max-w-4xl">
          <div className="label-kicker text-blue-200">Real-time career intelligence</div>
          <h2 className="mt-4 text-5xl md:text-7xl font-black tracking-tight leading-[0.86] holo-text">
            Less noise. More signal.
          </h2>
          <p className="mt-5 max-w-2xl text-base md:text-lg font-medium leading-relaxed text-blue-100">
            The interface stays calm while the system does the heavy lifting: parsing, scoring, trend extraction, gap analysis, and project planning.
          </p>
        </div>
      </section>
    </main>
  );
}
