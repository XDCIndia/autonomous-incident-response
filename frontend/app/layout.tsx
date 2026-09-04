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
          <div className="cine-bg" aria-hidden />
          <div className="cine-grid" aria-hidden />
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
