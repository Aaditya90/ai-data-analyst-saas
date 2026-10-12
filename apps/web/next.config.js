/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Phase 18: self-contained server bundle for the production Docker image.
  output: "standalone",
};

module.exports = nextConfig;
