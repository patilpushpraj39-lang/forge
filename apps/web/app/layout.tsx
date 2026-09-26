import type { Metadata } from "next";
import type { ReactNode } from "react";
import { ClerkProvider } from "@clerk/nextjs";

import "./styles.css";

export const metadata: Metadata = {
  title: "Forge Run Console",
  description: "Inspect a durable Forge walking-skeleton run"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  return (
    <html lang="en">
      <body>
        {publishableKey ? (
          <ClerkProvider publishableKey={publishableKey}>{children}</ClerkProvider>
        ) : children}
      </body>
    </html>
  );
}
