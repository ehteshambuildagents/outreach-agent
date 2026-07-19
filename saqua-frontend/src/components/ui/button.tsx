"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "relative isolate inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-200 ease-smooth focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-line focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-accent text-[color:var(--accent-ink)] font-semibold shadow-[0_1px_2px_rgba(15,23,42,.12)] hover:bg-accent-hi hover:-translate-y-px hover:shadow-card-lg active:translate-y-0",
        secondary:
          "border border-border bg-card text-text shadow-card hover:-translate-y-px hover:border-border-strong hover:bg-hover",
        ghost: "text-text-2 hover:bg-hover hover:text-text",
        outline: "border border-border text-text hover:-translate-y-px hover:border-border-strong hover:bg-hover",
        danger: "border border-danger-soft bg-danger-soft text-danger hover:-translate-y-px hover:bg-danger/15",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-9 px-4",
        lg: "h-11 px-6 text-[15px]",
        icon: "h-9 w-9 p-0",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
