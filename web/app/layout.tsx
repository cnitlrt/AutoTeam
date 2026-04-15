import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/components/providers/auth-provider";
import { ThemeProvider } from "@/components/providers/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

export const metadata: Metadata = {
  title: "AutoTeam 控制台",
  description: "ChatGPT Team 账号自动轮转管理",
};

const noFlashScript = `
(function(){try{
  var t = localStorage.getItem('autoteam_theme');
  if (!t) t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  var r = document.documentElement;
  if (t === 'light') r.classList.remove('dark'); else r.classList.add('dark');
  r.style.colorScheme = t;
}catch(e){}})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: noFlashScript }} />
      </head>
      <body className="min-h-screen">
        <ThemeProvider>
          <TooltipProvider delayDuration={150}>
            <AuthProvider>{children}</AuthProvider>
            <Toaster position="top-right" richColors />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
