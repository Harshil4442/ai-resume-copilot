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
        "animate-pulse rounded-md bg-slate-200/60",
        className
      ))}
    />
  );
}
