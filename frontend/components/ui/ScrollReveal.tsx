"use client";

import { motion } from "framer-motion";
import useMotionPreference from "./useMotionPreference";

interface ScrollRevealProps {
  children: React.ReactNode;
  className?: string;
  delay?: number;
  direction?: "up" | "down" | "left" | "right";
  width?: "fit-content" | "100%";
}

export default function ScrollReveal({ 
  children, 
  className = "", 
  delay = 0,
  direction = "up",
  width = "100%"
}: ScrollRevealProps) {
  
  const reducedMotion = useMotionPreference();
  const yOffset = direction === "up" ? 20 : direction === "down" ? -20 : 0;
  const xOffset = direction === "left" ? 20 : direction === "right" ? -20 : 0;

  return (
    <motion.div
      initial={false}
      whileInView={reducedMotion ? { opacity: 1, y: 0, x: 0 } : { opacity: 1, y: [yOffset, 0], x: [xOffset, 0] }}
      viewport={{ once: true, margin: "-50px" }}
      transition={{ 
        duration: reducedMotion ? 0 : 0.65,
        ease: [0.22, 1, 0.36, 1], 
        delay: reducedMotion ? 0 : delay
      }}
      style={{ width }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
