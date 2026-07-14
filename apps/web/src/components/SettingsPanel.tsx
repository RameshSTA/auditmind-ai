"use client";

/**
 * Settings — a real, client-side preference (theme), not a placeholder screen. There is no
 * backend user-preferences store, so this is deliberately the one kind of setting that's honest
 * to build without one: it changes nothing anyone else can see, persists to this browser only
 * (localStorage), and the toggle takes effect on the whole already-rendered app immediately by
 * flipping `data-theme` on <html> — every color in globals.css is a CSS custom property that
 * responds to it, not just this page.
 */
import { useEffect, useState } from "react";

const THEME_KEY = "auditmind:theme";
type Theme = "light" | "dark";

function applyTheme(theme: Theme) {
  if (theme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  window.localStorage.setItem(THEME_KEY, theme);
}

export function SettingsPanel() {
  const [theme, setTheme] = useState<Theme>("light");
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_KEY);
    setTheme(stored === "dark" ? "dark" : "light");
    setHydrated(true);
  }, []);

  function choose(next: Theme) {
    setTheme(next);
    applyTheme(next);
  }

  return (
    <section>
      <h2>Appearance</h2>
      <p className="lede mt-1">
        Applies immediately and is remembered on this device only — there is no
        account-level preferences store yet, so this doesn&apos;t follow you to another browser.
      </p>
      <div className="card mt-4 max-w-[420px]">
        <div className="kicker mb-2.5">Theme</div>
        <div className="flex gap-2.5">
          <button
            className={theme === "light" && hydrated ? "btn btn-primary" : "btn"}
            onClick={() => choose("light")}
          >
            Light
          </button>
          <button
            className={theme === "dark" && hydrated ? "btn btn-primary" : "btn"}
            onClick={() => choose("dark")}
          >
            Dark
          </button>
        </div>
      </div>
    </section>
  );
}
