"use client";

import { motion } from "framer-motion";
import { ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";
import Link from "next/link";

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
  
  const baseClasses = "inline-flex items-center justify-center whitespace-nowrap text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 h-11 px-6 py-2 rounded-xl group";
  
  const variants = {
    primary: "bg-primary text-white hover:bg-primary/90 shadow-md hover:shadow-lg shadow-primary/20",
    secondary: "bg-slate-800 text-white hover:bg-slate-200 shadow-sm",
    outline: "border border-slate-700 bg-transparent hover:bg-slate-900/50 text-white",
  };

  const content = (
    <>
      <span>{children}</span>
      {showArrow && (
        <ArrowRight className="ml-2 h-4 w-4 transition-transform duration-300 ease-in-out group-hover:translate-x-1" />
      )}
    </>
  );

  const motionProps = {
    whileTap: { scale: disabled ? 1 : 0.97 },
    transition: { type: "spring" as const, stiffness: 400, damping: 17 }
  };

  if (href) {
    return (
      <Link href={href} passHref legacyBehavior>
        <motion.a
          {...motionProps}
          className={twMerge(clsx(baseClasses, variants[variant], className))}
        >
          {content}
        </motion.a>
      </Link>
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
