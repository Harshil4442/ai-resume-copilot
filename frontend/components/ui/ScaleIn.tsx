"use client";

import { motion } from "framer-motion";
import useMotionPreference from "./useMotionPreference";

interface ScaleInProps {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}

export default function ScaleIn({ children, delay = 0, className = "" }: ScaleInProps) {
  const reducedMotion = useMotionPreference();
  return (
    <motion.div
      initial={false}
      whileInView={reducedMotion ? { opacity: 1, scale: 1, y: 0 } : { opacity: 1, scale: [0.98, 1], y: [10, 0] }}
      viewport={{ once: true }}
      transition={{
        duration: reducedMotion ? 0 : 0.5,
        ease: [0.22, 1, 0.36, 1],
        delay: reducedMotion ? 0 : delay,
      }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
