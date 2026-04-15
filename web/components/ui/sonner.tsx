"use client";
import { Toaster as Sonner } from "sonner";
import type { ComponentProps } from "react";

type ToasterProps = ComponentProps<typeof Sonner>;

export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="dark"
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast bg-card/90 border border-border/60 text-foreground backdrop-blur-xl shadow-lg",
          description: "text-muted-foreground",
          actionButton: "bg-primary text-primary-foreground",
          cancelButton: "bg-muted text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}

export { toast } from "sonner";
