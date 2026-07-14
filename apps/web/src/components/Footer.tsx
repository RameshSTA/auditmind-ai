/**
 * Site footer — quiet, consistent with the rest of the minimal design. Separated from the page
 * above by a background tint rather than a hairline rule (no horizontal lines in header/footer,
 * per design instruction). Two inline SVG marks rather than an icon library dependency — the app
 * only ever needs these two icons, so a whole package for that isn't worth the added dependency
 * surface.
 */
export function Footer() {
  return (
    <footer className="site-footer bg-surface">
      <div className="shell flex flex-wrap items-center justify-between gap-3 py-6">
        <span className="muted">AuditMind AI — Enterprise Agentic AI Audit Intelligence Platform</span>
        <div className="flex items-center gap-5">
          <span className="muted">Built by Ramesh Shrestha</span>
          <a
            href="https://www.linkedin.com/in/rameshsta"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-sm text-ink hover:underline"
          >
            <LinkedInIcon />
            LinkedIn
          </a>
          <a
            href="https://github.com/RameshSTA"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-sm text-ink hover:underline"
          >
            <GitHubIcon />
            GitHub
          </a>
        </div>
      </div>
    </footer>
  );
}

function LinkedInIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.45 20.45h-3.55v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.36V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.45v6.29ZM5.34 7.43a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12ZM7.12 20.45H3.56V9h3.56v11.45Z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M12 2C6.48 2 2 6.58 2 12.21c0 4.5 2.87 8.32 6.84 9.67.5.1.68-.22.68-.5 0-.24-.01-.87-.01-1.71-2.78.62-3.37-1.37-3.37-1.37-.45-1.18-1.11-1.5-1.11-1.5-.91-.64.07-.62.07-.62 1 .07 1.53 1.05 1.53 1.05.9 1.57 2.34 1.12 2.91.85.09-.67.35-1.12.64-1.38-2.22-.26-4.56-1.14-4.56-5.07 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.72 0 0 .84-.28 2.75 1.05a9.3 9.3 0 0 1 5 0c1.91-1.33 2.75-1.05 2.75-1.05.55 1.42.2 2.46.1 2.72.64.72 1.03 1.63 1.03 2.75 0 3.94-2.34 4.8-4.57 5.06.36.32.68.94.68 1.9 0 1.37-.01 2.47-.01 2.81 0 .28.18.61.69.5A10.02 10.02 0 0 0 22 12.21C22 6.58 17.52 2 12 2Z"
      />
    </svg>
  );
}
