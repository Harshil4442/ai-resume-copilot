"use client";

import { ReactNode } from "react";
import ShimmerBadge from "./ShimmerBadge";
import GradientHeading from "./GradientHeading";
import StaggerContainer, { StaggerItem } from "./StaggerContainer";
import { twMerge } from "tailwind-merge";
import clsx from "clsx";

interface PageHeaderProps {
  badge?: string;
  title: string;
  subtitle?: string | ReactNode;
  actions?: ReactNode;
  className?: string;
}

export default function PageHeader({ 
  badge, 
  title, 
  subtitle, 
  actions,
  className 
}: PageHeaderProps) {
  
  return (
    <div className={twMerge(clsx("relative py-8 md:py-12 max-w-4xl px-0", className))}>
      <StaggerContainer staggerDelay={0.15}>
        {badge && (
          <StaggerItem className="mb-4 flex">
            <ShimmerBadge showArrow={false}>{badge}</ShimmerBadge>
          </StaggerItem>
        )}
        
        <StaggerItem>
          <GradientHeading className="mb-4 text-3xl md:text-4xl lg:text-5xl">{title}</GradientHeading>
        </StaggerItem>
        
        {subtitle && (
          <StaggerItem>
            <p className="max-w-2xl text-base md:text-lg text-neutral-400 text-balance leading-relaxed">
              {subtitle}
            </p>
          </StaggerItem>
        )}
        
        {actions && (
          <StaggerItem className="mt-7 flex flex-wrap items-center gap-3">
            {actions}
          </StaggerItem>
        )}
      </StaggerContainer>
    </div>
  );
}
