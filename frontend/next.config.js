/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,

  // One front door. The browser only ever talks to this site's own origin; any
  // request starting with /api/ is forwarded to the backend server-side, so the
  // backend's address is never shipped to the visitor and nothing crosses
  // origins. BACKEND_ORIGIN has no NEXT_PUBLIC_ prefix, so it never reaches the
  // downloaded JS -- only the proxy hop knows where the backend lives.
  //
  // Note: this destination is resolved once, at BUILD time, and frozen into
  // .next/routes-manifest.json. Changing BACKEND_ORIGIN therefore requires a
  // redeploy, not a restart, and it must be set as a build-time variable on
  // Vercel. Set it at runtime only and every /api/* call silently proxies to
  // localhost:8000 instead.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.BACKEND_ORIGIN || 'http://localhost:8000'}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
