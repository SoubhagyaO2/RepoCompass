/** @type {import('next').NextConfig} */
const nextConfig = {
  // /api/* is forwarded to the RepoCompass API by app/api/[...path]/route.ts, which reads
  // REPOCOMPASS_API_URL at request time (a rewrite here would freeze it at build time).
};

export default nextConfig;
