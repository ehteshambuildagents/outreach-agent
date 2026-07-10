"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "relative isolate inline-flex items-center justify-center gap-2 overflow-hidden whitespace-nowrap rounded-sm text-sm font-medium transition-all duration-200 ease-smooth before:absolute before:inset-0 before:-z-10 before:scale-0 before:rounded-full before:bg-white/10 before:opacity-0 before:transition-all before:duration-300 hover:before:scale-150 hover:before:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-line disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-accent text-white font-semibold shadow-[0_1px_0_rgba(255,255,255,.12)_inset] hover:bg-accent-hi hover:-translate-y-px active:translate-y-0",
        secondary:
          "border border-border bg-white/[0.03] text-text hover:-translate-y-px hover:border-border-strong hover:bg-white/[0.06]",
        ghost: "text-text-2 hover:bg-white/[0.05] hover:text-text",
        outline: "border border-border text-text hover:-translate-y-px hover:bg-white/[0.04]",
        danger: "border border-danger-soft bg-danger-soft text-danger hover:-translate-y-px hover:bg-danger/20",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-9 px-4",
        lg: "h-10 px-5",
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
