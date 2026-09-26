"use client";

import { SignInButton, SignUpButton, UserButton, useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

import { RunConsole, RunConsoleAuthentication } from "./run-console";


function ClerkRunConsole() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const getAccessToken = useCallback(() => getToken(), [getToken]);
  const authentication: RunConsoleAuthentication = !isLoaded
    ? { status: "loading" }
    : isSignedIn
      ? { status: "signed-in", getAccessToken }
      : { status: "signed-out" };

  const controls = !isLoaded ? (
    <span className="auth-note">Loading session…</span>
  ) : isSignedIn ? (
    <div className="auth-controls">
      <span className="auth-note">Signed in</span>
      <UserButton />
    </div>
  ) : (
    <div className="auth-controls">
      <SignInButton mode="modal">
        <button className="secondary compact-button" type="button">Sign in</button>
      </SignInButton>
      <SignUpButton mode="modal">
        <button className="compact-button" type="button">Create account</button>
      </SignUpButton>
    </div>
  );

  return <RunConsole authentication={authentication} authControl={controls} />;
}


export function ForgeConsole() {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
    return <RunConsole authentication={{ status: "unconfigured" }} />;
  }
  return <ClerkRunConsole />;
}
