"use client";

import { motion } from "framer-motion";
import { ReactNode } from "react";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface StaggerContainerProps {
  children: ReactNode;
  staggerDelay?: number;
  className?: string;
  delayChildren?: number;
}

export default function StaggerContainer({
  children,
  staggerDelay = 0.1,
  className,
  delayChildren = 0,
}: StaggerContainerProps) {
  const containerVariants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: staggerDelay,
        delayChildren: delayChildren,
      },
    },
  };

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: "-50px" }}
      className={twMerge(clsx(className))}
    >
      {children}
    </motion.div>
  );
}

export const StaggerItem = ({ children, className }: { children: ReactNode, className?: string }) => {
  const itemVariants = {
    hidden: { opacity: 0, y: 20 },
    show: { 
      opacity: 1, 
      y: 0,
      transition: {
        type: "spring" as const,
        bounce: 0.2,
        duration: 0.6
      }
    },
  };

  return (
    <motion.div variants={itemVariants} className={twMerge(clsx(className))}>
      {children}
    </motion.div>
  );
};
