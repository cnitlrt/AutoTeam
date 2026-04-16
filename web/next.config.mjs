/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  eslint: { ignoreDuringBuilds: true },
  // noVNC's util/browser.js uses top-level await for WebCodecs feature detection.
  transpilePackages: ["@novnc/novnc"],
  webpack(config) {
    config.experiments = { ...(config.experiments || {}), topLevelAwait: true };
    return config;
  },
};

export default nextConfig;
