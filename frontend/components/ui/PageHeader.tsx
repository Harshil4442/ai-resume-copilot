"use client";

import { ReactNode } from "react";
import ShimmerBadge from "./ShimmerBadge";
import GradientHeading from "./GradientHeading";
import FadeIn from "./FadeIn";
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
    <div className={twMerge(clsx("relative py-12 md:py-20 text-center max-w-4xl mx-auto px-4", className))}>
      <StaggerContainer staggerDelay={0.15}>
        {badge && (
          <StaggerItem className="mb-6 flex justify-center">
            <ShimmerBadge showArrow={false}>{badge}</ShimmerBadge>
          </StaggerItem>
        )}
        
        <StaggerItem>
          <GradientHeading className="mb-6">{title}</GradientHeading>
        </StaggerItem>
        
        {subtitle && (
          <StaggerItem>
            <p className="text-lg md:text-xl text-slate-500 text-balance tracking-tight max-w-2xl mx-auto leading-relaxed">
              {subtitle}
            </p>
          </StaggerItem>
        )}
        
        {actions && (
          <StaggerItem className="mt-10 flex flex-wrap items-center justify-center gap-4">
            {actions}
          </StaggerItem>
        )}
      </StaggerContainer>
    </div>
  );
}
