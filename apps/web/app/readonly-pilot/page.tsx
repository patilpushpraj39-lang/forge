import type { Metadata } from "next";
import { ReadonlyPilotReview } from "./review";

export const metadata: Metadata = {
  title: "Forge · Record-only request review",
  description: "Review an immutable README request without executing AI or authorizing spending."
};

export default async function ReadonlyPilotPage({ searchParams }: {
  searchParams: Promise<{ run?: string; repository?: string }>
}) {
  const query = await searchParams;
  return <ReadonlyPilotReview initialRun={typeof query.run === "string" ? query.run : ""}
    initialRepository={typeof query.repository === "string" ? query.repository : ""} />;
}
