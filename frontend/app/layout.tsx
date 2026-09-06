import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/lib/theme-context";

const geistSans = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

export const metadata: Metadata = {
  title: "System Bachao — Your System. Protected by AI.",
  description:
    "Autonomous enterprise incident response. A self-healing AI core that detects, investigates and remediates service failures in real time.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} theme-dark`}>
      <body>
        <ThemeProvider>
          {/* Layered premium AI/SaaS background */}
          <div className="bg-base" aria-hidden />
          <div className="bg-grid" aria-hidden />
          <div className="bg-glow-hero" aria-hidden />
          <div className="bg-blob bg-blob-1" aria-hidden />
          <div className="bg-blob bg-blob-2" aria-hidden />
          <div className="bg-blob bg-blob-3" aria-hidden />
          <div className="bg-particles" aria-hidden />
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
