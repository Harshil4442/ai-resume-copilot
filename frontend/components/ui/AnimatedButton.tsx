"use client";

import { motion } from "framer-motion";
import useMotionPreference from "./useMotionPreference";
import { ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";
import Link from "next/link";

const MotionLink = motion.create(Link);

interface AnimatedButtonProps {
  children: ReactNode;
  href?: string;
  onClick?: () => void;
  className?: string;
  variant?: "primary" | "secondary" | "outline";
  showArrow?: boolean;
  type?: "button" | "submit" | "reset";
  disabled?: boolean;
}

export default function AnimatedButton({
  children,
  href,
  onClick,
  className,
  variant = "primary",
  showArrow = false,
  type = "button",
  disabled = false,
}: AnimatedButtonProps) {
  const reducedMotion = useMotionPreference();
  const baseClasses = "inline-flex items-center justify-center whitespace-nowrap text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 min-h-11 px-6 py-2.5 rounded-full group";
  
  const variants = {
    primary: "bg-primary text-primary-foreground hover:bg-[#39490d]",
    secondary: "bg-accent/20 text-foreground hover:bg-accent/30",
    outline: "border border-border bg-white hover:border-accent hover:bg-surface text-foreground",
  };

  const content = (
    <>
      <span>{children}</span>
      {showArrow && (
        <ArrowRight aria-hidden="true" className="ml-2 h-4 w-4 transition-transform duration-300 ease-in-out motion-safe:group-hover:translate-x-1" />
      )}
    </>
  );

  const motionProps = {
    whileTap: { scale: disabled || reducedMotion ? 1 : 0.98 },
    transition: { duration: reducedMotion ? 0 : 0.15 }
  };

  if (href) {
    return (
      <MotionLink
        href={href}
        {...motionProps}
        className={twMerge(clsx(baseClasses, variants[variant], className))}
      >
        {content}
      </MotionLink>
    );
  }

  return (
    <motion.button
      {...motionProps}
      onClick={onClick}
      type={type}
      disabled={disabled}
      className={twMerge(clsx(baseClasses, variants[variant], className))}
    >
      {content}
    </motion.button>
  );
}
