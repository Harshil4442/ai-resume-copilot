"use client";

import { motion } from "framer-motion";

interface ScaleInProps {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}

export default function ScaleIn({ children, delay = 0, className = "" }: ScaleInProps) {
  return (
    <motion.div
      initial={{ opacity: 1, scale: 0.95, y: 10 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{
        type: "spring",
        stiffness: 100,
        damping: 20,
        delay: delay,
      }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
