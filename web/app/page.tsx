"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sparkles } from "lucide-react";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/dashboard");
  }, [router]);
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="flex items-center gap-3 text-muted-foreground">
        <Sparkles className="h-5 w-5 animate-pulse" />
        <span>加载中...</span>
      </div>
    </div>
  );
}
