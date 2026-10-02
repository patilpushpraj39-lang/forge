"use client";

import { SignInButton, SignUpButton, UserButton, useAuth } from "@clerk/nextjs";
import { createContext, Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiAccessError, createApiClient } from "../lib/api-client";
import type { ApiAuthentication } from "../lib/api-client";

const unconfigured: ApiAuthentication = { status: "unconfigured" };
const SessionContext = createContext<{ authentication: ApiAuthentication; reportDenial?: (status: 401 | 403) => void }>({ authentication: unconfigured });
export const webApiMode = process.env.NEXT_PUBLIC_FORGE_API_MODE ?? "development";
const apiBase = process.env.NEXT_PUBLIC_FORGE_API_URL ?? "http://localhost:8000";

function ClerkSession({ children }: { children: ReactNode }) {
  const { getToken, isLoaded, isSignedIn, sessionId, userId } = useAuth();
  const sessionKey = `${userId}:${sessionId}`;
  const [denial, setDenial] = useState<{ key: string; status: 401 | 403 } | null>(null);
  const accessDenied = denial?.key === sessionKey ? denial.status : undefined;
  const reportDenial = useCallback((status: 401 | 403) => setDenial(current =>
    current?.key === sessionKey && current.status === status ? current : { key: sessionKey, status }), [sessionKey]);
  const getAccessToken = useCallback((forceRefresh = false) => getToken({ skipCache: forceRefresh }), [getToken]);
  const authentication = useMemo<ApiAuthentication>(() => !isLoaded
    ? { status: "loading" }
    : isSignedIn
      ? { status: "signed-in", sessionKey, getAccessToken, accessDenied }
      : { status: "signed-out" }, [accessDenied, getAccessToken, isLoaded, isSignedIn, sessionKey]);
  const context = useMemo(() => ({ authentication, reportDenial }), [authentication, reportDenial]);
  return (
    <SessionContext.Provider value={context}>
      {/* A changed identity remounts data consumers, clearing evidence and aborting old requests. */}
      <Fragment key={`${authentication.sessionKey ?? authentication.status}:${accessDenied ?? "active"}`}>{children}</Fragment>
    </SessionContext.Provider>
  );
}

export function ApiSessionProvider({ children }: { children: ReactNode }) {
  return process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
    ? <ClerkSession>{children}</ClerkSession> : children;
}

export function useApiSession() { return useContext(SessionContext).authentication; }

export function useApiClient() {
  const { authentication, reportDenial } = useContext(SessionContext);
  const lifetime = useRef(new AbortController());
  const client = useMemo(() => createApiClient(apiBase, authentication, webApiMode), [authentication]);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, [client]);
  const reportError = useCallback((error: unknown) => {
    if (error instanceof ApiAccessError && (error.status === 401 || error.status === 403)) reportDenial?.(error.status);
    throw error;
  }, [reportDenial]);
  return useMemo(() => ({
    ready: client.ready,
    request: (path: string, init: RequestInit = {}, requireSession = false) => client.request(path, {
      ...init, signal: AbortSignal.any([lifetime.current.signal, ...(init.signal ? [init.signal] : [])])
    }, requireSession).catch(reportError),
    stream: (runId: string, options: Parameters<typeof client.stream>[1]) => client.stream(runId, {
      ...options, signal: AbortSignal.any([lifetime.current.signal, options.signal])
    }).catch(reportError)
  }), [client, reportError]);
}

export function ReviewerControls() {
  const { status, accessDenied } = useApiSession();
  if (status === "unconfigured") return <span className="auth-note">{webApiMode === "controlled"
    ? "Configure reviewer sign-in before accessing this protected API."
    : "Trusted local development · sign-in not configured"}</span>;
  if (status === "loading") return <span className="auth-note">Loading session…</span>;
  if (status === "signed-in") return <div className="auth-controls"><span className="auth-note">{accessDenied === 401
    ? "Session expired. Sign out, then sign in again."
    : accessDenied === 403 ? "This account is not an authorized reviewer. Switch accounts."
    : "Signed in"}</span><UserButton /></div>;
  return <div className="auth-controls">
    <SignInButton mode="modal"><button className="secondary compact-button" type="button">Sign in</button></SignInButton>
    <SignUpButton mode="modal"><button className="compact-button" type="button">Create account</button></SignUpButton>
  </div>;
}
