"use client";

import { ReactNode } from "react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface MarqueeProps {
  children: ReactNode;
  className?: string;
  reverse?: boolean;
  pauseOnHover?: boolean;
  repeat?: number;
}

export default function Marquee({
  children,
  className,
  reverse = false,
  pauseOnHover = true,
  repeat = 4,
}: MarqueeProps) {
  return (
    <div
      className={twMerge(clsx(
        "group flex overflow-hidden p-2 [--gap:1rem] [gap:var(--gap)] flex-row [--duration:40s]",
        className
      ))}
    >
      {Array.from({ length: repeat }).map((_, i) => (
        <div
          key={i}
          className={twMerge(clsx(
            "flex shrink-0 justify-around [gap:var(--gap)] animate-marquee flex-row",
            pauseOnHover && "group-hover:[animation-play-state:paused]",
            reverse && "[animation-direction:reverse]"
          ))}
        >
          {children}
        </div>
      ))}
    </div>
  );
}
