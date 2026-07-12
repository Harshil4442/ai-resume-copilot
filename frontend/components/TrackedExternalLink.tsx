"use client";

import type { AnchorHTMLAttributes, ReactNode } from "react";
import { trackEvent } from "../lib/analytics";

type TrackedExternalLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  children: ReactNode;
  eventName?: string;
  eventParams?: Record<string, string | number | boolean | null | undefined>;
};

export default function TrackedExternalLink({
  children,
  eventName = "external_link_clicked",
  eventParams = {},
  onClick,
  target = "_blank",
  rel = "noopener noreferrer",
  ...props
}: TrackedExternalLinkProps) {
  return (
    <a
      {...props}
      target={target}
      rel={rel}
      onClick={(event) => {
        trackEvent(eventName, {
          href: props.href,
          ...eventParams,
        });
        onClick?.(event);
      }}
    >
      {children}
    </a>
  );
}
