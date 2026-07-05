"use client";

import { ReactNode } from "react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface GlassCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  className?: string;
  hoverEffect?: boolean;
}

export default function GlassCard({ 
  children, 
  className, 
  hoverEffect = true,
  ...props
}: GlassCardProps) {
  
  return (
    <div
      className={twMerge(clsx(
        "rounded-2xl border border-slate-800/60 bg-slate-900/40 backdrop-blur-xl p-6 shadow-sm",
        hoverEffect && "transition-all duration-300 ease-out hover:bg-slate-900/60 hover:shadow-[0_8px_30px_rgb(59,130,246,0.15)] hover:-translate-y-1 hover:border-slate-700",
        className
      ))}
      {...props}
    >
      {children}
    </div>
  );
}
