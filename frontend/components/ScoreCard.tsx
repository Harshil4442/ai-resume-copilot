export default function ScoreCard({
  title,
  value,
  subtitle,
}: { title: string; value: string; subtitle?: string }) {
  return (
    <div className="metric-card kinetic-border tilt-lift overflow-hidden">
      <div className="flex items-center justify-between gap-3">
        <div className="text-[11px] font-black uppercase tracking-[0.18em] text-slate-500">{title}</div>
        <span className="pulse-dot" />
      </div>
      <div className="text-4xl font-black mt-3 text-slate-950 ink-gradient">{value}</div>
      {subtitle && <div className="text-xs font-medium text-slate-500 mt-2 leading-relaxed">{subtitle}</div>}
    </div>
  );
}
