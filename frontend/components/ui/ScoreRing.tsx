"use client";

import { motion } from "framer-motion";
import useMotionPreference from "./useMotionPreference";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface ScoreRingProps {
  score: number; // 0 to 100
  size?: number;
  strokeWidth?: number;
  className?: string;
  showText?: boolean;
}

export default function ScoreRing({
  score,
  size = 120,
  strokeWidth = 8,
  className,
  showText = true,
}: ScoreRingProps) {
  const reducedMotion = useMotionPreference();
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const offset = ((100 - score) / 100) * circumference;

  let colorClass = "text-primary";
  if (score < 50) colorClass = "text-coral";
  else if (score < 80) colorClass = "text-[#96702d]";

  return (
    <div className={twMerge(clsx("relative inline-flex items-center justify-center", className))} style={{ width: size, height: size }}>
      <svg className="transform -rotate-90" width={size} height={size}>
        <circle
          className="text-border"
          strokeWidth={strokeWidth}
          stroke="currentColor"
          fill="transparent"
          r={radius}
          cx={size / 2}
          cy={size / 2}
        />
        <motion.circle
          className={colorClass}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          initial={false}
          whileInView={{ strokeDashoffset: reducedMotion ? offset : [circumference, offset] }}
          viewport={{ once: true }}
          transition={{ duration: reducedMotion ? 0 : 1, ease: "easeOut" }}
          strokeLinecap="round"
          stroke="currentColor"
          fill="transparent"
          r={radius}
          cx={size / 2}
          cy={size / 2}
        />
      </svg>
      {showText && (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-display text-3xl font-normal text-foreground leading-none">{score}</span>
        </div>
      )}
    </div>
  );
}
