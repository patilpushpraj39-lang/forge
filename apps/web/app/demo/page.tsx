import type { Metadata } from "next";

import { OfflineDemo } from "./offline-demo";


export const metadata: Metadata = {
  title: "Forge Offline Demo",
  description: "Explore Forge's approval workflow without an API key or model calls"
};

export default function DemoPage() {
  if (process.env.NEXT_PUBLIC_FORGE_API_MODE === "controlled") {
    return <main className="benchmark-page"><h1>Offline demo disabled</h1><p>The controlled deployment only accepts installed GitHub repositories. Use the trusted local development profile for the offline demo.</p></main>;
  }
  return <OfflineDemo />;
}
