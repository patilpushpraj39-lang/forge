import type { Metadata } from "next";

import { OfflineDemo } from "./offline-demo";


export const metadata: Metadata = {
  title: "Forge Offline Demo",
  description: "Explore Forge's approval workflow without an API key or model calls"
};

export default function DemoPage() {
  return <OfflineDemo />;
}
