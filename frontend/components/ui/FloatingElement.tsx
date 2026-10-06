"use client";

import { motion } from "framer-motion";
import useMotionPreference from "./useMotionPreference";
import { ReactNode } from "react";

interface FloatingElementProps {
  children: ReactNode;
  className?: string;
  delay?: number;
  yOffset?: number;
  duration?: number;
}

export default function FloatingElement({ 
  children, 
  className = "",
  delay = 0,
  yOffset = 15,
  duration = 4
}: FloatingElementProps) {
  const reducedMotion = useMotionPreference();
  return (
    <motion.div
      animate={{ y: reducedMotion ? 0 : [0, -yOffset, 0] }}
      transition={{
        duration: reducedMotion ? 0 : duration,
        repeat: reducedMotion ? 0 : Infinity,
        ease: "easeInOut",
        delay: reducedMotion ? 0 : delay
      }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
