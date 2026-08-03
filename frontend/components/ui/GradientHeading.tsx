"use client";

import { ReactNode } from "react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface GradientHeadingProps {
  children: ReactNode;
  className?: string;
  element?: "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
}

export default function GradientHeading({ 
  children, 
  className, 
  element: Element = "h1" 
}: GradientHeadingProps) {
  
  return (
    <Element
      className={twMerge(clsx(
        "text-4xl md:text-5xl lg:text-6xl text-[#f4f2ea] text-balance leading-[1.05] font-black",
        className
      ))}
    >
      {children}
    </Element>
  );
}
