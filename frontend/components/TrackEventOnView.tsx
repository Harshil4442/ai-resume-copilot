"use client";

import { useEffect } from "react";
import { trackEvent } from "../lib/analytics";

type TrackEventOnViewProps = {
  eventName: string;
  params?: Record<string, string | number | boolean | null | undefined>;
};

export default function TrackEventOnView({ eventName, params = {} }: TrackEventOnViewProps) {
  useEffect(() => {
    trackEvent(eventName, params);
  }, [eventName, params]);

  return null;
}
