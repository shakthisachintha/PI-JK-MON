"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useState } from "react";

// Loaded only in `bun run dev`; never fetched by the production build.
const Devtools =
  process.env.NODE_ENV === "development"
    ? dynamic(() => import("@tanstack/react-query-devtools").then((m) => m.ReactQueryDevtools), { ssr: false })
    : () => null;

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            // Polling pauses while the tab is hidden (spec §9.2) and resumes with a refetch on focus.
            refetchIntervalInBackground: false,
            refetchOnWindowFocus: true,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      {children}
      <Devtools buttonPosition="bottom-left" />
    </QueryClientProvider>
  );
}
