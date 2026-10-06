import { cn } from "../../lib/cn";

const tones = {
  neutral: "border-border bg-surface text-muted-foreground",
  teal: "border-primary/20 bg-primary/8 text-primary",
  amber: "border-[#d7c49b] bg-[#f9f4e8] text-[#866224]",
  coral: "border-coral/20 bg-coral/5 text-coral",
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
    <span className={cn("inline-flex min-h-6 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium", tones[tone], className)}>
      <span className="status-dot" aria-hidden="true" />
      {children}
    </span>
  );
}
