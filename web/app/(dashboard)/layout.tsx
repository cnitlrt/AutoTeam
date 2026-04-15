"use client";
import { Sidebar, MobileNav } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { PageTransition } from "@/components/layout/page-transition";
import { useAuth } from "@/components/providers/auth-provider";
import { Skeleton } from "@/components/ui/skeleton";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { state, authRequired } = useAuth();
  if (state === "loading" || state === "needs-setup" || state === "unauthenticated") {
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <Skeleton className="h-16 w-64" />
      </div>
    );
  }
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 min-w-0 px-4 md:px-8 py-6 pb-24 md:pb-8">
        <div className="max-w-7xl mx-auto">
          <Topbar authRequired={authRequired} />
          <PageTransition>{children}</PageTransition>
        </div>
      </main>
      <MobileNav />
    </div>
  );
}
