import type { Metadata, Viewport } from "next";

import { AppHeader } from "@/components/AppHeader";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Ray Vault",
  description: "Battery (JK BMS) and inverter (SRNE) monitoring and control",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f5f7" },
    { media: "(prefers-color-scheme: dark)", color: "#0c1015" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <AppHeader />
          {children}
        </Providers>
      </body>
    </html>
  );
}
