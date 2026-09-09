"use client";

import { useState, type ReactNode } from "react";
import { CineNetworkBackground } from "@/components/CineNetworkBackground";
import { ThemeToggle } from "@/components/ThemeToggle";
import { IconMenu } from "@/components/icons";
import { Sidebar } from "./Sidebar";

/** The authenticated console application shell: persistent sidebar (fixed on
 * desktop, drawer on smaller screens), a slim topbar with the page title and
 * room for page-specific status controls (`headerExtra`), and the page body.
 * Wrap every protected dashboard page in this — it replaces the per-page
 * glass-nav headers. */
export function ConsoleShell({
  title,
  headerExtra,
  children,
}: {
  title: string;
  headerExtra?: ReactNode;
  children: ReactNode;
}) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="min-h-screen">
      <CineNetworkBackground />

      {/* Desktop sidebar — persistent */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[232px] lg:block">
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      {mobileNavOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
            onClick={() => setMobileNavOpen(false)}
            aria-hidden
          />
          <aside className="absolute inset-y-0 left-0 w-[240px] shadow-2xl">
            <Sidebar onNavigate={() => setMobileNavOpen(false)} />
          </aside>
        </div>
      )}

      {/* Content column */}
      <div className="lg:pl-[232px]">
        <header className="glass-nav sticky top-0 z-30 flex items-center gap-3 px-4 py-2.5">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation menu"
            className="grid h-8 w-8 place-items-center rounded-md border border-[var(--color-border-default)] text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-emphasis)] hover:text-[var(--color-text-primary)] lg:hidden"
          >
            <IconMenu />
          </button>
          <h1 className="truncate text-[14px] font-semibold tracking-[0.08em] text-[var(--color-text-primary)]">
            {title}
          </h1>
          <div className="ml-auto flex shrink-0 items-center gap-3">
            {headerExtra}
            <ThemeToggle />
          </div>
        </header>
        <main className="mx-auto max-w-[1280px] px-4 py-6">{children}</main>
      </div>
    </div>
  );
}
