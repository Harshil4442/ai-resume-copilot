import type { LucideIcon } from "lucide-react";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center border-y border-white/10 px-6 py-12 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-md bg-white/5 text-neutral-400">
        <Icon size={21} aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-lg font-black text-neutral-100">{title}</h2>
      <p className="mt-2 max-w-md text-sm leading-6 text-neutral-500">{description}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
