"use client";

/**
 * Fades in the page content on route change — the "page transitions" half of the design
 * philosophy's "motion only to orient" rule (§1-2). Keying on pathname forces React to remount
 * this wrapper on navigation, which retriggers the CSS animation (globals.css's `.fade-in`);
 * layout.tsx itself never remounts between routes, so without this the content would just swap
 * instantly with no cue that a new page loaded.
 */
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} className="fade-in">
      {children}
    </div>
  );
}
