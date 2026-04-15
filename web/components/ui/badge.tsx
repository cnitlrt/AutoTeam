import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/10 text-primary",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        destructive: "border-destructive/30 bg-destructive/10 text-destructive",
        outline: "border-border/70 text-foreground",
        success:
          "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
        warning:
          "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
        info: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
        muted: "border-border/50 bg-muted/50 text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
