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
        "rounded-lg border border-white/10 bg-[#171a19] p-6 shadow-[0_1px_0_rgba(255,255,255,0.03)]",
        hoverEffect && "transition-colors duration-200 hover:border-primary/50 hover:bg-[#1b1f1d]",
        className
      ))}
      {...props}
    >
      {children}
    </div>
  );
}
