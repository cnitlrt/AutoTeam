import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const alertVariants = cva(
  "relative w-full rounded-lg border p-4 text-sm [&>svg]:absolute [&>svg]:left-4 [&>svg]:top-4 [&>svg~*]:pl-7",
  {
    variants: {
      variant: {
        default: "bg-card/70 border-border/60 text-foreground",
        destructive:
          "border-destructive/40 bg-destructive/10 text-destructive [&>svg]:text-destructive",
        warning:
          "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300 [&>svg]:text-amber-500 dark:[&>svg]:text-amber-400",
        success:
          "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 [&>svg]:text-emerald-500 dark:[&>svg]:text-emerald-400",
        info:
          "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300 [&>svg]:text-sky-500 dark:[&>svg]:text-sky-400",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

const Alert = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof alertVariants>
>(({ className, variant, ...props }, ref) => (
  <div ref={ref} role="alert" className={cn(alertVariants({ variant }), className)} {...props} />
));
Alert.displayName = "Alert";

const AlertTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h5
      ref={ref}
      className={cn("mb-1 font-medium leading-none tracking-tight", className)}
      {...props}
    />
  ),
);
AlertTitle.displayName = "AlertTitle";

const AlertDescription = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("text-sm [&_p]:leading-relaxed", className)} {...props} />
  ),
);
AlertDescription.displayName = "AlertDescription";

export { Alert, AlertTitle, AlertDescription };
