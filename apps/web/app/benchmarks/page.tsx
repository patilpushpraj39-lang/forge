import type { Metadata } from "next";

import { BenchmarkDashboard } from "./benchmark-dashboard";


export const metadata: Metadata = {
  title: "Forge Failure Analysis",
  description: "Inspect source-backed Forge benchmark outcomes and failures"
};

export default function BenchmarksPage() {
  return <BenchmarkDashboard />;
}
