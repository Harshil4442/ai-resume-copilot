import { cn } from "../../lib/cn";

const tones = {
  neutral: "border-white/10 bg-white/5 text-neutral-400",
  teal: "border-primary/25 bg-primary/10 text-[#67debd]",
  amber: "border-accent/25 bg-accent/10 text-[#f5d98f]",
  coral: "border-coral/25 bg-coral/10 text-[#ffab9e]",
};

export function StatusBadge({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode;
  tone?: keyof typeof tones;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex min-h-6 items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-bold", tones[tone], className)}>
      <span className="status-dot" aria-hidden="true" />
      {children}
    </span>
  );
}
