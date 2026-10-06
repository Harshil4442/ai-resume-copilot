import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

const buttonVariants = cva(
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-full px-5 text-sm font-medium transition-[background-color,border-color,color,transform] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        primary: "border border-transparent bg-primary text-primary-foreground hover:bg-[#39490d] motion-safe:hover:-translate-y-0.5 active:translate-y-0",
        secondary: "border border-border bg-white text-foreground hover:border-accent hover:bg-surface",
        ghost: "border border-transparent bg-transparent text-muted-foreground hover:bg-surface hover:text-foreground",
        danger: "border border-coral/25 bg-coral/5 text-coral hover:bg-coral/10",
      },
      size: {
        sm: "min-h-8 px-3 text-xs",
        md: "min-h-10 px-4 text-sm",
        icon: "h-10 min-h-10 w-10 px-0",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Component = asChild ? Slot : "button";
  return <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
