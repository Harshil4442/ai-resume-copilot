"use client";

import GlassCard from "./ui/GlassCard";
import { LucideIcon } from "lucide-react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

export default function ScoreCard({
  title,
  value,
  subtitle,
  icon: Icon,
  className,
  valueClassName
}: { 
  title: string; 
  value: string | React.ReactNode; 
  subtitle?: string;
  icon?: LucideIcon;
  className?: string;
  valueClassName?: string;
}) {
  return (
    <GlassCard className={twMerge(clsx("flex flex-col p-5", className))}>
      <div className="flex items-center gap-2 mb-3 text-slate-400">
        {Icon && <Icon size={16} className="text-primary" />}
        <div className="text-xs font-bold uppercase tracking-wider">{title}</div>
      </div>
      <div className={twMerge(clsx("text-3xl font-black text-white tracking-tighter mb-2", valueClassName))}>
        {value}
      </div>
      {subtitle && (
        <div className="text-sm font-medium text-slate-400 leading-relaxed">
          {subtitle}
        </div>
      )}
    </GlassCard>
  );
}
