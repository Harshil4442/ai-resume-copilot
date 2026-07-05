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
        hoverEffect && "transition-all duration-300 ease-out hover:bg-white/90 hover:shadow-[0_8px_30px_rgb(0,113,227,0.1)] hover:-translate-y-1 hover:border-blue-200/50",
        className
      ))}
      {...props}
    >
      {children}
    </div>
  );
}
