/**
 * Next.js configuration.
 *
 * `reactStrictMode` stays on — it surfaces accidental side effects in effects/renders during dev,
 * the same "catch it in development, not in front of an auditor" posture the backend takes with
 * mypy --strict and RLS-proven-not-assumed.
 *
 * There is deliberately no rewrites()/proxy to the FastAPI API here: every browser-visible call
 * goes through this app's own Route Handlers under /api/* (the BFF), which attach the bearer token
 * server-side. The browser never learns the API's address or holds a token (Phase 2 §7, Phase 13
 * §12). The API base URL is read from AUDITMIND_API_BASE_URL at request time in the BFF only.
 *
 * @type {import('next').NextConfig}
 */
const nextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
