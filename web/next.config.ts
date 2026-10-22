import type { NextConfig } from "next";

// Static export: `next build` writes plain HTML/JS/CSS to out/, which
// scripts/copy-static.mjs copies into the Python package (ray_vault/static).
// The Pi serves it with FastAPI; no Node.js runs on the Pi.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true, // /battery/ -> battery/index.html, which FastAPI's StaticFiles serves as a directory index
  images: { unoptimized: true },
  poweredByHeader: false,
};

export default nextConfig;
