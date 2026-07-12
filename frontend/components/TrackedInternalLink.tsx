"use client";

import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

import { trackEvent } from "../lib/analytics";

type TrackedInternalLinkProps = ComponentProps<typeof Link> & {
  children: ReactNode;
  eventName: string;
  eventParams?: Record<string, string | number | boolean | null | undefined>;
};

export default function TrackedInternalLink({
  children,
  eventName,
  eventParams = {},
  onClick,
  ...props
}: TrackedInternalLinkProps) {
  return (
    <Link
      {...props}
      onClick={(event) => {
        trackEvent(eventName, eventParams);
        onClick?.(event);
      }}
    >
      {children}
    </Link>
  );
}
