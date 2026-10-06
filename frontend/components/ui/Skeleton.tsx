"use client";

import { twMerge } from "tailwind-merge";
import clsx from "clsx";

export default function Skeleton({
  className,
}: {
  className?: string;
}) {
  return (
    <div
      className={twMerge(clsx(
        "motion-safe:animate-pulse rounded-xl bg-[#edf0e7]",
        className
      ))}
    />
  );
}
