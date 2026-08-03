import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

const buttonVariants = cva(
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        primary: "border border-transparent bg-primary text-[#06110d] hover:bg-[#52dab6]",
        secondary: "border border-white/15 bg-[#1b1f1d] text-neutral-100 hover:border-white/25 hover:bg-white/5",
        ghost: "border border-transparent bg-transparent text-neutral-300 hover:bg-white/5 hover:text-white",
        danger: "border border-coral/45 bg-coral/10 text-[#ffb0a3] hover:bg-coral/20",
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
