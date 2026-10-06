"use client";

import { motion } from "framer-motion";
import useMotionPreference from "./useMotionPreference";
import { ReactNode } from "react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface FadeInProps {
  children: ReactNode;
  delay?: number;
  direction?: "up" | "down" | "left" | "right" | "none";
  className?: string;
  duration?: number;
}

export default function FadeIn({
  children,
  delay = 0,
  direction = "up",
  className,
  duration = 0.7,
}: FadeInProps) {
  const reducedMotion = useMotionPreference();
  const directionOffset = {
    up: { y: 24, x: 0 },
    down: { y: -24, x: 0 },
    left: { x: 24, y: 0 },
    right: { x: -24, y: 0 },
    none: { x: 0, y: 0 },
  };

  return (
    <motion.div
      initial={false}
      whileInView={reducedMotion ? { opacity: 1, x: 0, y: 0 } : {
        opacity: 1,
        x: [directionOffset[direction].x, 0],
        y: [directionOffset[direction].y, 0],
      }}
      viewport={{ once: true, margin: "-50px" }}
      transition={{
        duration: reducedMotion ? 0 : duration,
        delay: reducedMotion ? 0 : delay,
        ease: [0.22, 1, 0.36, 1],
      }}
      className={twMerge(clsx(className))}
    >
      {children}
    </motion.div>
  );
}
