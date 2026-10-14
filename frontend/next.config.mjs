// The browser talks to /api/*; Next forwards it to the FastAPI backend, so no CORS is needed.
const backend = process.env.REPOCOMPASS_API_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
