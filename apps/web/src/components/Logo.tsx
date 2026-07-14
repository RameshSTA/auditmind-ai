/**
 * Wordmark + icon mark. A shield (assurance/audit) with a checkmark (a finding that's been signed
 * off, not just flagged) — the same "confirmed, not just asserted" idea the sign-off gate enforces
 * throughout the product. Pure `currentColor` SVG, no fill values baked in, so it inherits
 * `text-ink` and repaints correctly across the light/dark theme without a second asset.
 */
export function LogoMark({ size = 24 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M12 2.5 4 5.5v5.2c0 5.1 3.4 9.4 8 10.8 4.6-1.4 8-5.7 8-10.8V5.5L12 2.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M8.5 12.2 11 14.7l4.5-5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="brand">AuditMind AI</span>
    </span>
  );
}
