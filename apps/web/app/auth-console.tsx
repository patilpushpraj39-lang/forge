"use client";

import { RunConsole } from "./run-console";
import { ReviewerControls, useApiSession } from "./api-session";

export function ForgeConsole() {
  return <RunConsole authentication={useApiSession()} authControl={<ReviewerControls />} />;
}
