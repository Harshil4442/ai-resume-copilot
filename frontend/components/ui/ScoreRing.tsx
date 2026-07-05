"use client";

import { motion } from "framer-motion";
import { useEffect, useState } from "react";
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
  const [offset, setOffset] = useState(0);
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  
  useEffect(() => {
    const progressOffset = ((100 - score) / 100) * circumference;
    setOffset(progressOffset);
  }, [score, circumference]);

  let colorClass = "text-emerald-500";
  if (score < 50) colorClass = "text-rose-500";
  else if (score < 80) colorClass = "text-amber-500";

  return (
    <div className={twMerge(clsx("relative inline-flex items-center justify-center", className))} style={{ width: size, height: size }}>
      <svg className="transform -rotate-90" width={size} height={size}>
        <circle
          className="text-slate-100"
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
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1.5, type: "spring" as const, bounce: 0.1 }}
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
          <span className="text-2xl font-black text-slate-900 tracking-tighter leading-none">{score}</span>
        </div>
      )}
    </div>
  );
}
