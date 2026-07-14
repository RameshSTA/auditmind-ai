import type { Config } from "tailwindcss";

/**
 * Minimal design tokens — a restrained, near-monochrome palette: black/white/gray carries the
 * interface, color is spent only where it's load-bearing (risk severity). This file is the
 * single source of truth for the values themselves.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  // `@layer components` selectors are themselves subject to Tailwind's content-based purge, same
  // as any utility (documented Tailwind v3 behavior) — a hand-authored `.sev-critical` rule is
  // dropped entirely unless the literal string "sev-critical" appears somewhere in `content`.
  // Every OTHER custom class in globals.css (`.card`, `.btn`, `.kicker`, ...) is written as a
  // static string directly in JSX (`className="card"`) so the scanner finds it naturally. These
  // seven are the only ones built dynamically (`` `sev-${severity}` ``, `` `status-${status}` ``
  // in ui.tsx's SeverityChip/StatusText) — the literal token never appears anywhere, so without
  // this safelist the whole rule silently vanishes. Confirmed by isolating a minimal repro (a
  // plain, @apply-free `@layer components { .sev-critical { color: red } }` still compiled to
  // nothing without the class name appearing in content) before writing this comment.
  safelist: [
    "sev-critical",
    "sev-high",
    "sev-medium",
    "sev-low",
    "status-draft",
    "status-confirmed",
    "status-rejected",
    "status-open",
    "status-true_positive",
    "status-false_positive",
    "status-pending",
    "status-running",
    "status-awaiting_human_review",
    "status-completed",
    "status-cannot_conclude",
    "status-halted",
    "status-failed",
    "status-approve",
    "status-reject",
    "status-edit",
    "status-closed",
  ],
  theme: {
    extend: {
      colors: {
        // Every value routes through a CSS custom property (defined in globals.css's `:root` /
        // `[data-theme="dark"]`), not a static hex — this is what lets Settings' theme toggle
        // (SettingsPanel.tsx) re-theme the whole already-rendered app instantly by flipping one
        // `data-theme` attribute on <html>, rather than needing every component to know about
        // light/dark itself. tailwind.config.ts stays the single source of truth for token
        // *names*; globals.css is the single source of truth for their *values* per theme.
        //
        // The `rgb(var(--x) / <alpha-value>)` wrapper (not a bare `var(--x)`) is required, not
        // stylistic: it's the one color-definition shape Tailwind will combine with an opacity
        // modifier like `ring-ink/10` — confirmed by a production build failing with "the class
        // `ring-ink/10` does not exist" the one time this used bare `var()` references instead.
        // It requires globals.css's custom properties to hold space-separated "R G B" triplets,
        // not hex strings — see that file's comment for why.
        ink: {
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          secondary: "rgb(var(--ink-secondary) / <alpha-value>)",
          muted: "rgb(var(--ink-muted) / <alpha-value>)",
        },
        line: "rgb(var(--line) / <alpha-value>)",
        "line-strong": "rgb(var(--line-strong) / <alpha-value>)",
        paper: "rgb(var(--paper) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        risk: {
          critical: "rgb(var(--risk-critical) / <alpha-value>)",
          "critical-bg": "rgb(var(--risk-critical-bg) / <alpha-value>)",
          high: "rgb(var(--risk-high) / <alpha-value>)",
          "high-bg": "rgb(var(--risk-high-bg) / <alpha-value>)",
          medium: "rgb(var(--risk-medium) / <alpha-value>)",
          "medium-bg": "rgb(var(--risk-medium-bg) / <alpha-value>)",
          low: "rgb(var(--risk-low) / <alpha-value>)",
          "low-bg": "rgb(var(--risk-low-bg) / <alpha-value>)",
        },
        // The topbar's masthead color (globals.css's `.topbar`) — deliberately invariant across
        // light/dark theme, same reasoning as the risk tones: it's brand identity, not a surface
        // that should flip with the reader's theme preference.
        brand: "rgb(var(--brand) / <alpha-value>)",
      },
      fontFamily: {
        // Self-hosted via next/font/google in layout.tsx (--font-inter / --font-jetbrains-mono) —
        // never a runtime Google Fonts request, so this works offline and has no CSP/render-
        // blocking cost. The literal family names are the fallback if the CSS variable is ever
        // unset (e.g. a component rendered outside RootLayout in a test).
        sans: [
          "var(--font-inter)",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["var(--font-jetbrains-mono)", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: {
        DEFAULT: "6px",
      },
    },
  },
  plugins: [],
};

export default config;
