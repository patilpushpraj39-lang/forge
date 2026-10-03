"use client";

import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { useApiClient, webApiMode } from "./api-session";
import type { ApiAuthentication } from "../lib/api-client";
import { checkReviewerAccess, reviewerAccessError, reviewerAccessStatus } from "../lib/reviewer-access";
import type { ReviewerIdentity } from "../lib/reviewer-access";
import { githubCatalogError } from "../lib/github-catalog";
import { shouldLoadRunReview } from "../lib/run-review";

type Run = {
  run_id: string;
  repository_path: string;
  repository_owner: string | null;
  repository_name: string | null;
  installation_id: number | null;
  base_ref: string | null;
  base_sha: string | null;
  objective: string;
  state: string;
  evaluated_patch_hash?: string | null;
  evaluation_verdict_hash?: string | null;
};

type RunEvent = {
  event_id: string;
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
};

type RepositoryBrief = {
  manifest_hash: string;
  indexed_file_count: number;
  ignored_path_count: number;
  binary_file_count: number;
  oversized_file_count: number;
  languages: Record<string, number>;
  build_systems: string[];
  test_commands: string[];
};

type Review = {
  patch_hash: string;
  verdict_hash: string;
  verdict: string;
  changed_paths: string[];
  checks: Array<{
    check_id: string;
    kind: string;
    status: string;
    required: boolean;
  }>;
  rubric: { passed?: boolean; scores?: Record<string, number>; notes?: string[] };
  patch: string;
  repository: {
    owner: string;
    name: string;
    installation_id: number;
    base_ref: string;
    base_sha: string;
  };
};

type Approval = { approval_id: string; actor_id: string; expires_at: string };
type Publication = {
  publication_id: string;
  status: string;
  branch_name: string;
  github_pull_request_url?: string | null;
};

type GitHubInstallation = {
  installation_id: number;
  account_login: string;
  account_type: string;
  repository_selection: string;
};

type GitHubRepository = {
  owner: string;
  name: string;
  full_name: string;
  default_branch: string;
  private: boolean;
};

export type RunConsoleAuthentication = ApiAuthentication;
const terminalStates = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `API returned ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: string };
    if (payload.detail) message = payload.detail;
  } catch {
    // The status code remains useful when the server did not return JSON.
  }
  throw new Error(message);
}

function requestKey(prefix: string): string {
  return `${prefix}:${crypto.randomUUID()}`;
}

export function RunConsole({
  authentication,
  authControl
}: {
  authentication: RunConsoleAuthentication;
  authControl?: ReactNode;
}) {
  const api = useApiClient();
  const [repositoryPath, setRepositoryPath] = useState("");
  const [objective, setObjective] = useState("");
  const [publishToGitHub, setPublishToGitHub] = useState(webApiMode === "controlled");
  const [installations, setInstallations] = useState<GitHubInstallation[]>([]);
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [installationId, setInstallationId] = useState("");
  const [repositoryName, setRepositoryName] = useState("");
  const [baseRef, setBaseRef] = useState("main");
  const [installationsBusy, setInstallationsBusy] = useState(false);
  const [repositoriesBusy, setRepositoriesBusy] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const catalogBusy = publishToGitHub && (installationsBusy || repositoriesBusy);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [review, setReview] = useState<Review | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [publication, setPublication] = useState<Publication | null>(null);
  const [approvalRequestKey, setApprovalRequestKey] = useState<string | null>(null);
  const [publicationRequestKey, setPublicationRequestKey] = useState<string | null>(null);
  const [reviewerIdentity, setReviewerIdentity] = useState<ReviewerIdentity | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [prTitle, setPrTitle] = useState("Forge: verified engineering fix");
  const [prBody, setPrBody] = useState(
    "This patch was generated in an isolated Forge workspace and passed independent evaluation."
  );
  const [error, setError] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const reviewerStatus = reviewerAccessStatus(authentication, reviewerIdentity, authError);

  const runId = run?.run_id;
  const needsReview = shouldLoadRunReview(run, events);
  const repositoryBrief = events.find(
    (event) => event.event_type === "repository_indexed"
  )?.payload.brief as RepositoryBrief | undefined;
  const canCancel = Boolean(
    run && !terminalStates.has(run.state) && run.state !== "PUBLISHING"
  );
  const eventSummary = useMemo(() => [...events].reverse().slice(0, 8), [events]);
  const selectedRepository = repositories.find(
    (repository) => repository.full_name === repositoryName
  );

  useEffect(() => {
    if (!api.ready) return;
    const restoredRunId = new URLSearchParams(window.location.search).get("run");
    if (!restoredRunId) return;
    let cancelled = false;
    const controller = new AbortController();
    api.request(`/runs/${encodeURIComponent(restoredRunId)}`, { signal: controller.signal })
      .then((response) => responseJson<Run>(response))
      .then((restoredRun) => {
        if (!cancelled) {
          setRepositoryPath(restoredRun.repository_path);
          setObjective(restoredRun.objective);
          setRun(restoredRun);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Unable to restore run");
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [api]);

  useEffect(() => {
    if (!publishToGitHub || installations.length > 0) {
      setInstallationsBusy(false);
      if (!publishToGitHub) setCatalogError(null);
      return;
    }
    if (!api.ready || authentication.status !== "signed-in" || !authentication.getAccessToken) {
      setInstallationsBusy(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setCatalogError(null);
    setInstallationsBusy(true);
    api.request("/github/installations", { signal: controller.signal }, true)
      .then((response) => responseJson<GitHubInstallation[]>(response))
      .then((items) => {
        if (cancelled) return;
        setInstallations(items);
        if (items.length > 0) setInstallationId(String(items[0].installation_id));
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setCatalogError(githubCatalogError(caught));
        }
      })
      .finally(() => {
        if (!cancelled) setInstallationsBusy(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    api,
    authentication.getAccessToken,
    authentication.status,
    installations.length,
    publishToGitHub
  ]);

  useEffect(() => {
    if (!publishToGitHub || !installationId) {
      setRepositoriesBusy(false);
      setRepositories([]);
      setRepositoryName("");
      return;
    }
    if (!api.ready || authentication.status !== "signed-in" || !authentication.getAccessToken) {
      setRepositoriesBusy(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setCatalogError(null);
    setRepositories([]);
    setRepositoryName("");
    setRepositoriesBusy(true);
    api.request(`/github/installations/${encodeURIComponent(installationId)}/repositories`, { signal: controller.signal }, true)
      .then((response) => responseJson<GitHubRepository[]>(response))
      .then((items) => {
        if (cancelled) return;
        setRepositories(items);
        const first = items[0];
        setRepositoryName(first?.full_name ?? "");
        setBaseRef(first?.default_branch ?? "main");
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setCatalogError(githubCatalogError(caught));
        }
      })
      .finally(() => {
        if (!cancelled) setRepositoriesBusy(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    api,
    authentication.getAccessToken,
    authentication.status,
    installationId,
    publishToGitHub
  ]);

  useEffect(() => {
    if (!runId || !api.ready) return;
    const controller = new AbortController();
    void api.stream(runId, { signal: controller.signal, onStatus: setStreamError, onEvent: (event) => {
      setEvents((current) =>
        current.some((item) => item.event_id === event.event_id)
          ? current
          : [...current, event]
      );
      if (event.event_type === "state_changed") {
        setRun((current) =>
          current
            ? { ...current, state: String(event.payload.to_state ?? current.state) }
            : current
        );
      }
      if (event.event_type === "pull_request_created") {
        setPublication((current) =>
          current
            ? {
                ...current,
                status: "COMPLETED",
                github_pull_request_url: String(event.payload.pull_request_url)
              }
            : current
        );
      }
    } }).catch((caught: unknown) => {
      if (!controller.signal.aborted) setStreamError(caught instanceof Error ? caught.message : "Unable to load live progress.");
    });
    return () => controller.abort();
  }, [api, runId]);

  useEffect(() => {
    if (
      !api.ready || !runId ||
      !needsReview
    ) {
      setReview(null);
      return;
    }
    const controller = new AbortController();
    api.request(`/runs/${encodeURIComponent(runId)}/review`, { signal: controller.signal })
      .then((response) => responseJson<Review>(response))
      .then((evidence) => { if (!controller.signal.aborted) setReview(evidence); })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Unable to load review evidence");
      });
    return () => controller.abort();
  }, [api, runId, run?.state, needsReview]);

  useEffect(() => {
    if (!api.ready || authentication.status !== "signed-in" || !authentication.getAccessToken) {
      setReviewerIdentity(null);
      setAuthError(null);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setReviewerIdentity(null);
    setAuthError(null);
    checkReviewerAccess(api.request, controller.signal)
      .then((identity) => {
        if (!cancelled) {
          setReviewerIdentity(identity);
          setAuthError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setReviewerIdentity(null);
          setAuthError(reviewerAccessError(caught));
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [api, authentication.status, authentication.getAccessToken]);

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("create");
    setError(null);
    setRun(null);
    setEvents([]);
    setReview(null);
    setApproval(null);
    setPublication(null);
    setApprovalRequestKey(null);
    setPublicationRequestKey(null);
    try {
      if (publishToGitHub && !selectedRepository) {
        throw new Error("Select an accessible GitHub repository");
      }
      const endpoint = publishToGitHub ? "/github/runs" : "/runs";
      const body = publishToGitHub
        ? {
            installation_id: Number(installationId),
            owner: selectedRepository?.owner,
            name: selectedRepository?.name,
            base_ref: baseRef,
            objective
          }
        : { repository_path: repositoryPath, objective };
      const response = await api.request(endpoint, {
        method: "POST",
        headers: {
          "content-type": "application/json"
        },
        body: JSON.stringify(body)
      }, publishToGitHub);
      const createdRun = await responseJson<Run>(response);
      window.history.replaceState(
        null,
        "",
        `?run=${encodeURIComponent(createdRun.run_id)}`
      );
      setRun(createdRun);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create run");
    } finally {
      setBusy(null);
    }
  }

  async function cancelRun() {
    if (!run) return;
    setBusy("cancel");
    try {
      const response = await api.request(`/runs/${encodeURIComponent(run.run_id)}/cancel`, {
        method: "POST"
      });
      setRun(await responseJson<Run>(response));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to cancel run");
    } finally {
      setBusy(null);
    }
  }

  async function approvePatch() {
    if (!run || !review || !confirmed || !reviewerIdentity) return;
    setBusy("approve");
    setError(null);
    const idempotencyKey = approvalRequestKey ?? requestKey("approval");
    setApprovalRequestKey(idempotencyKey);
    try {
      const response = await api.request(`/runs/${encodeURIComponent(run.run_id)}/approvals`, {
        method: "POST",
        headers: {
          "content-type": "application/json"
        },
        body: JSON.stringify({
          patch_hash: review.patch_hash,
          evaluation_verdict_hash: review.verdict_hash,
          approval_key: idempotencyKey,
          expires_in_seconds: 900
        })
      }, true);
      setApproval(await responseJson<Approval>(response));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to approve patch");
    } finally {
      setBusy(null);
    }
  }

  async function publishPatch() {
    if (!run || !review || !approval) return;
    setBusy("publish");
    setError(null);
    const idempotencyKey = publicationRequestKey ?? requestKey("publication");
    setPublicationRequestKey(idempotencyKey);
    try {
      const response = await api.request(`/runs/${encodeURIComponent(run.run_id)}/publish`, {
        method: "POST",
        headers: {
          "content-type": "application/json"
        },
        body: JSON.stringify({
          approval_id: approval.approval_id,
          patch_hash: review.patch_hash,
          title: prTitle,
          body: prBody,
          idempotency_key: idempotencyKey
        })
      }, true);
      setPublication(await responseJson<Publication>(response));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to request publication");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="console" aria-label="Forge run console">
      <div className="console-auth">
        <div>
          <span className="label">Reviewer access</span>
          <p className={reviewerStatus.failed ? "error compact-error" : undefined}
            role={reviewerStatus.failed ? "alert" : "status"}>{reviewerStatus.message}</p>
        </div>
        {authControl}
      </div>
      {!api.ready ? <p className="auth-note" role="status">Sign in as an authorized reviewer to load protected run evidence.</p> : null}
      <form onSubmit={createRun} className="run-form">
        <div className="section-title">
          <span className="step">01</span>
          <div>
            <span className="label">Bounded input</span>
            <h2>Create an engineering run</h2>
          </div>
        </div>
        {!publishToGitHub ? (
          <>
            <label htmlFor="repository-path">Local repository path</label>
            <input
              id="repository-path"
              value={repositoryPath}
              onChange={(event) => setRepositoryPath(event.target.value)}
              placeholder="C:\\projects\\sample-repository"
              required
            />
          </>
        ) : null}
        <label htmlFor="objective">Engineering task</label>
        <textarea
          id="objective"
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
          placeholder="Fix the authentication redirect, add a regression test, and explain the root cause."
          maxLength={10000}
          required
        />
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={publishToGitHub}
            disabled={webApiMode === "controlled"}
            onChange={(event) => {
              setPublishToGitHub(event.target.checked);
              setCatalogError(null);
            }}
          />
          <span>
            <strong>Use an installed GitHub repository</strong>
            <small>
              Forge resolves the branch once, verifies every Git object, and stores an immutable snapshot.
            </small>
          </span>
        </label>
        {publishToGitHub ? (
          <div className="github-grid">
            <label>
              Installation
              <select
                value={installationId}
                onChange={(event) => setInstallationId(event.target.value)}
                disabled={catalogBusy || !reviewerIdentity}
                required
              >
                <option value="">Select an installation</option>
                {installations.map((installation) => (
                  <option key={installation.installation_id} value={installation.installation_id}>
                    {installation.account_login} · {installation.account_type}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Repository
              <select
                value={repositoryName}
                onChange={(event) => {
                  const value = event.target.value;
                  setRepositoryName(value);
                  const repository = repositories.find((item) => item.full_name === value);
                  if (repository) setBaseRef(repository.default_branch);
                }}
                disabled={catalogBusy || !reviewerIdentity || !installationId}
                required
              >
                <option value="">Select a repository</option>
                {repositories.map((repository) => (
                  <option key={repository.full_name} value={repository.full_name}>
                    {repository.full_name}{repository.private ? " · private" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="wide">
              Base branch
              <input value={baseRef} onChange={(event) => setBaseRef(event.target.value)} required />
            </label>
            <p className={catalogError ? "catalog-note error compact-error" : "catalog-note"}
              role={catalogError ? "alert" : "status"}>
              {authentication.status === "unconfigured"
                ? "Configure Clerk before connecting an installed GitHub repository."
                : authentication.status !== "signed-in"
                  ? "Sign in as an authorized reviewer to load GitHub installations."
                  : !reviewerIdentity
                    ? reviewerStatus.message
                    : catalogBusy
                ? "Loading authorized GitHub targets…"
                : catalogError
                  ? catalogError
                : installations.length === 0
                  ? "No active GitHub App installation is available."
                  : repositories.length === 0
                    ? "This installation has no usable repository."
                : "The exact commit SHA is resolved and stored by the server when this run is created."}
            </p>
          </div>
        ) : null}
        <div className="form-actions">
          <p>Hard limits protect cost, time, tool use, and patch attempts.</p>
          <button
            disabled={!api.ready || busy === "create" || catalogBusy || (publishToGitHub && (!reviewerIdentity || !selectedRepository || Boolean(catalogError)))}
            type="submit"
          >
            {busy === "create" ? "Capturing source…" : "Create bounded run"}
          </button>
        </div>
      </form>

      {error ? <p className="error" role="alert">{error}</p> : null}
      {streamError ? <p className="error" role="status">{streamError}</p> : null}

      {run ? (
        <section className="run-summary">
          <div>
            <span className="label">Run</span>
            <code>{run.run_id.slice(0, 12)}</code>
          </div>
          <div>
            <span className="label">State</span>
            <strong className={`state state-${run.state.toLowerCase()}`}>
              {run.state.replaceAll("_", " ")}
            </strong>
          </div>
          <div className="run-objective">
            <span className="label">Task</span>
            <p>{run.objective}</p>
          </div>
          {canCancel ? (
            <button className="secondary" onClick={cancelRun} disabled={busy === "cancel"} type="button">
              Cancel run
            </button>
          ) : null}
        </section>
      ) : null}

      {run?.state === "COMPLETED" && !needsReview ? (
        <p className="muted" role="status">Completed without a patch. No patch review or publication is expected.</p>
      ) : null}

      {repositoryBrief ? (
        <section className="repository-brief">
          <div className="brief-heading">
            <div>
              <span className="label">Repository intelligence</span>
              <h2>Immutable working context</h2>
            </div>
            <code>{repositoryBrief.manifest_hash.slice(0, 12)}</code>
          </div>
          <div className="brief-metrics">
            <div><strong>{repositoryBrief.indexed_file_count}</strong><span>files indexed</span></div>
            <div><strong>{repositoryBrief.ignored_path_count}</strong><span>paths ignored</span></div>
            <div><strong>{repositoryBrief.binary_file_count + repositoryBrief.oversized_file_count}</strong><span>safely excluded</span></div>
          </div>
          <div className="brief-details">
            <div><span className="label">Languages</span><p>{Object.entries(repositoryBrief.languages).map(([language, count]) => `${language} ${count}`).join(" · ") || "No text files detected"}</p></div>
            <div><span className="label">Build systems</span><p>{repositoryBrief.build_systems.join(" · ") || "Not detected"}</p></div>
            <div><span className="label">Verification</span><p>{repositoryBrief.test_commands.join(" · ") || "No standard test command detected"}</p></div>
          </div>
        </section>
      ) : null}

      {review ? (
        <section className="review-panel">
          <div className="section-title">
            <span className="step">02</span>
            <div><span className="label">Independent evaluation</span><h2>Review the exact patch</h2></div>
            <span className={`verdict verdict-${review.verdict}`}>{review.verdict}</span>
          </div>
          <div className="evidence-strip">
            <div><span>Patch</span><code>{review.patch_hash.slice(0, 12)}</code></div>
            <div><span>Verdict</span><code>{review.verdict_hash.slice(0, 12)}</code></div>
            <div><span>Base</span><code>{review.repository.base_sha?.slice(0, 12)}</code></div>
          </div>
          <div className="changed-files">
            {review.changed_paths.map((path) => <code key={path}>{path}</code>)}
          </div>
          <div className="checks">
            <h3>Verification checks</h3>
            {review.checks.map((check) => (
              <div className="check" key={check.check_id}>
                <span className={`check-dot check-${check.status}`} />
                <strong>{check.check_id}</strong>
                <span>{check.kind}</span>
                <em>{check.status}</em>
              </div>
            ))}
          </div>
          <details className="diff" open>
            <summary>Patch diff · {review.changed_paths.length} file{review.changed_paths.length === 1 ? "" : "s"}</summary>
            <pre>{review.patch}</pre>
          </details>

          {run?.state === "AWAITING_APPROVAL" ? (
            <div className="approval-box">
              <div className="section-title compact">
                <span className="step">03</span>
                <div><span className="label">Human gate</span><h2>Approve and publish</h2></div>
              </div>
              <div className="reviewer-session">
                <div>
                  <span className="label">Reviewer session</span>
                  <strong>
                    {reviewerStatus.message}
                  </strong>
                </div>
              </div>
              <label className="toggle-row confirmation">
                <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                <span>I reviewed the diff and checks. Approve only this patch hash, verdict, repository, and base commit for 15 minutes.</span>
              </label>
              {!approval ? (
                <button onClick={approvePatch} disabled={!confirmed || !reviewerIdentity || busy === "approve"} type="button">
                  {busy === "approve" ? "Approving…" : "Approve exact patch"}
                </button>
              ) : (
                <div className="publish-form">
                  <p className="success">Approval recorded for {approval.actor_id}. It expires at {new Date(approval.expires_at).toLocaleTimeString()}.</p>
                  <label>Pull request title<input value={prTitle} onChange={(event) => setPrTitle(event.target.value)} maxLength={256} /></label>
                  <label>Pull request body<textarea value={prBody} onChange={(event) => setPrBody(event.target.value)} maxLength={20000} /></label>
                  <button onClick={publishPatch} disabled={busy === "publish"} type="button">
                    {busy === "publish" ? "Requesting…" : "Create pull request"}
                  </button>
                </div>
              )}
            </div>
          ) : null}
          {publication ? <p className="success">Publication queued on <code>{publication.branch_name}</code>. The publisher will create at most one pull request.</p> : null}
          {publication?.github_pull_request_url ? <a className="pr-link" href={publication.github_pull_request_url} target="_blank" rel="noreferrer">Open pull request ↗</a> : null}
        </section>
      ) : null}

      {events.length ? (
        <section className="activity">
          <div className="section-title compact">
            <span className="step">04</span>
            <div><span className="label">Durable audit trail</span><h2>Recent activity</h2></div>
          </div>
          <ol className="timeline">
            {eventSummary.map((event) => (
              <li key={event.event_id}>
                <span className="sequence">{event.sequence}</span>
                <div>
                  <strong>{event.event_type.replaceAll("_", " ")}</strong>
                  <p>{JSON.stringify(event.payload)}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </section>
  );
}
