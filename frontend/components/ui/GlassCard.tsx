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
        "rounded-2xl border border-slate-200/60 bg-white/70 backdrop-blur-xl p-6 shadow-sm",
        hoverEffect && "transition-all duration-300 ease-out hover:bg-white/90 hover:shadow-lg hover:-translate-y-1 hover:border-slate-300/60",
        className
      ))}
      {...props}
    >
      {children}
    </div>
  );
}
