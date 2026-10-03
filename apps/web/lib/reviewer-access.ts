import { ApiAccessError } from "./api-client.ts";
import type { ApiAuthentication } from "./api-client.ts";

export type ReviewerIdentity = { actor_id: string; subject: string; provider: string };
type ReviewerRequest = (path: string, init: RequestInit, requireSession: boolean) => Promise<Response>;

export function reviewerAccessStatus(authentication: ApiAuthentication,
  identity: ReviewerIdentity | null, error: string | null): { message: string; failed: boolean } {
  if (authentication.status === "unconfigured") return { message: "Clerk is not configured", failed: false };
  if (authentication.status === "loading") return { message: "Loading reviewer session…", failed: false };
  if (authentication.status === "signed-out") return {
    message: "Sign in to access GitHub repositories and approve patches", failed: false
  };
  if (authentication.accessDenied === 401) return { message: "Session expired. Sign out, then sign in again.", failed: true };
  if (authentication.accessDenied === 403) return { message: "This account is not an authorized reviewer. Switch accounts.", failed: true };
  if (error) return { message: error, failed: true };
  return { message: identity?.actor_id ?? "Checking reviewer authorization…", failed: false };
}

export function reviewerAccessError(error: unknown): string {
  return error instanceof ApiAccessError ? error.message
    : "Unable to check reviewer access. Make sure the backend is running, then reload.";
}

// Bound the complete check, including token retrieval and response decoding.
// Aborting alone cannot settle an upstream getToken promise that ignores signals.
export async function checkReviewerAccess(request: ReviewerRequest, signal: AbortSignal,
  timeoutMs = 15_000): Promise<ReviewerIdentity> {
  if (signal.aborted) throw new DOMException("Request cancelled", "AbortError");
  const deadline = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel: (() => void) | undefined;
  const interrupted = new Promise<never>((_resolve, reject) => {
    cancel = () => reject(new DOMException("Request cancelled", "AbortError"));
    signal.addEventListener("abort", cancel, { once: true });
    timer = setTimeout(() => {
      deadline.abort();
      reject(new Error("Reviewer check timed out"));
    }, timeoutMs);
  });
  try {
    const result = request("/auth/me", { signal: AbortSignal.any([signal, deadline.signal]) }, true)
      .then(async response => {
        if (!response.ok) throw new ApiAccessError(response.status);
        const identity = await response.json() as ReviewerIdentity;
        if (!identity || [identity.actor_id, identity.subject, identity.provider]
          .some(value => typeof value !== "string" || !value)) throw new Error("Invalid reviewer identity");
        return identity;
      });
    return await Promise.race([result, interrupted]);
  } finally {
    clearTimeout(timer);
    if (cancel) signal.removeEventListener("abort", cancel);
    deadline.abort();
  }
}
