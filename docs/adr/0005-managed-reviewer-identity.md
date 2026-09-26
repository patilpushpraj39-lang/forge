# ADR 0005 Managed Reviewer Identity

- Status: accepted
- Date: 2026-09-26

## Context

An approval is a security decision that authorizes a GitHub write. A reviewer
identifier supplied in the JSON body is only a claim and cannot provide
authentication or authorization. Forge also should not implement password,
session, MFA, recovery, or social-login infrastructure as part of its product.

## Decision

Forge will use Clerk for interactive reviewer authentication. The Next.js app
obtains a short-lived session token and sends it as a bearer token only for
reviewer actions. FastAPI verifies the session through Clerk's official Python
SDK using a configured public JWT key, restricts tokens to known authorized
parties, accepts only session tokens, and authorizes subjects through an
explicit server-side reviewer allowlist.

The API derives the durable actor ID from the verified subject. Approval request
bodies cannot supply or override that identity. Publication requires the same
authenticated actor that granted the approval. When authentication is absent or
incomplete, reviewer actions fail closed.

## Alternatives

- Trust a reviewer string from the browser.
- Build passwords, sessions, and account recovery inside Forge.
- Accept any valid identity-provider user as an approver.
- Put identity verification only in the Next.js process and trust forwarded
  headers at the FastAPI boundary.

## Consequences

Local source and evaluation work remains usable without an identity provider,
but approval and publication require configured authentication. Deployments
must configure Clerk keys, an authorized-party allowlist, and explicit reviewer
subjects. Session tokens are verified but never stored. A later multi-tenant
version can replace the static allowlist with repository-scoped organization
permissions without changing the approval evidence contract.
