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
        "rounded-2xl border border-border bg-card p-6",
        hoverEffect && "transition-[border-color,box-shadow] duration-200 hover:border-accent hover:shadow-[0_8px_24px_rgba(72,92,17,0.04)]",
        className
      ))}
      {...props}
    >
      {children}
    </div>
  );
}
