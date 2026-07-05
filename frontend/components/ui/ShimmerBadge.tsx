"use client";

import { ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface ShimmerBadgeProps {
  children: ReactNode;
  href?: string;
  className?: string;
  showArrow?: boolean;
}

export default function ShimmerBadge({ children, href, className, showArrow = true }: ShimmerBadgeProps) {
  const Component = href ? "a" : "div";
  
  return (
    <Component
      href={href}
      className={twMerge(clsx(
        "inline-flex h-7 items-center justify-between rounded-full border border-slate-200 bg-white px-3 text-xs text-slate-900 transition-all ease-in hover:cursor-pointer hover:bg-slate-50 group gap-1 shadow-sm",
        className
      ))}
    >
      <p 
        className="mx-auto max-w-md animate-shimmer bg-clip-text bg-no-repeat bg-gradient-to-r from-slate-500 via-slate-950 via-50% to-slate-500 inline-flex items-center justify-center font-medium"
        style={{
          backgroundSize: "200% 100%",
        }}
      >
        <span>{children}</span>
        {showArrow && (
          <ArrowRight className="ml-1 h-3 w-3 transition-transform duration-300 ease-in-out group-hover:translate-x-0.5 text-slate-950" />
        )}
      </p>
    </Component>
  );
}
