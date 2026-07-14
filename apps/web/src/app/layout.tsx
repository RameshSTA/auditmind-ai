import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { Inter, JetBrains_Mono } from "next/font/google";

import { Providers } from "@/app/providers";
import { Avatar } from "@/components/ui";
import { CommandPalette } from "@/components/CommandPalette";
import { Footer } from "@/components/Footer";
import { Logo } from "@/components/Logo";
import { PageTransition } from "@/components/PageTransition";
import { Sidebar } from "@/components/Sidebar";
import { getSessionPersona } from "@/server/session";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AuditMind AI",
  description:
    "AuditMind AI is an enterprise audit intelligence platform: full-population evidence review, AI-assisted risk scoring, and fraud detection with every finding traceable to source evidence.",
};

/** Reads the stored theme preference (SettingsPanel.tsx writes it) and applies it before first
 * paint — a blocking inline script is the only way to avoid a light-then-dark flash on load,
 * since the preference lives in localStorage, which isn't available to a server component. */
const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem('auditmind:theme');if(t==='dark')document.documentElement.setAttribute('data-theme','dark');}catch(e){}`;

export default async function RootLayout({ children }: { children: ReactNode }) {
  const persona = await getSessionPersona();
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable}`}
      // THEME_INIT_SCRIPT sets `data-theme` on this element before React hydrates, from
      // localStorage the server can't see — an intentional, expected mismatch between what the
      // server rendered and what's in the DOM by the time React hydrates, not a real bug for
      // React to warn about. suppressHydrationWarning is scoped to this one element/attribute
      // only, the documented pattern for exactly this "theme script" case (the same one
      // next-themes itself uses), not a blanket suppression of real hydration issues elsewhere.
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="app-body">
        <header className="topbar">
          <div className="shell">
            <Link href={persona ? "/engagements" : "/"}>
              <Logo />
            </Link>
            {persona ? <CommandPalette /> : null}
            <div className="flex-1" />
            {persona ? (
              <div className="who">
                <Avatar name={persona.displayName} />
                <span>
                  {persona.displayName}
                  <span className="mono ml-2 text-ink-muted">{persona.tokenRoles.join(", ")}</span>
                </span>
                <form action="/api/auth/logout" method="post">
                  <button className="btn px-2.5 py-1.5" type="submit">
                    Sign out
                  </button>
                </form>
              </div>
            ) : (
              <Link href="/login" className="btn btn-primary px-3.5 py-1.5">
                Sign in
              </Link>
            )}
          </div>
        </header>
        <div className="app-main-row">
          {persona ? <Sidebar /> : null}
          <main className="main-content">
            <div className="shell py-10 pb-16">
              <Providers>
                <PageTransition>{children}</PageTransition>
              </Providers>
            </div>
          </main>
        </div>
        <Footer />
      </body>
    </html>
  );
}
