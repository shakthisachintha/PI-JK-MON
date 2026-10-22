"use client";

import { EventsTable } from "@/components/EventsTable";
import { SystemPanel } from "@/components/SystemPanel";
import { PageMain } from "@/components/ui";

export default function SystemPage() {
  return (
    <PageMain>
      <SystemPanel />
      <EventsTable limit={50} />
    </PageMain>
  );
}
