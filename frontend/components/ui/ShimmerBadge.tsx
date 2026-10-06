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
        "group inline-flex min-h-7 items-center justify-between gap-1 rounded-full border border-border bg-surface px-3 font-mono text-[11px] font-normal uppercase tracking-wide text-primary transition-colors",
        href && "hover:border-accent hover:bg-accent/15",
        className
      ))}
    >
      <span className="inline-flex items-center justify-center">
        <span>{children}</span>
        {showArrow && (
          <ArrowRight aria-hidden="true" className="ml-1 h-3 w-3 transition-transform duration-300 ease-in-out motion-safe:group-hover:translate-x-0.5" />
        )}
      </span>
    </Component>
  );
}
