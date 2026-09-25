"use client";

import { FormEvent, useEffect, useState } from "react";

type Run = {
  run_id: string;
  repository_path: string;
  state: string;
  created_at: string;
  updated_at: string;
};

type RunEvent = {
  event_id: string;
  run_id: string;
  sequence: number;
  schema_version: 1;
  event_type: string;
  occurred_at: string;
  actor: string;
  payload: Record<string, unknown>;
};

const apiBase =
  process.env.NEXT_PUBLIC_FORGE_API_URL ?? "http://localhost:8000";

export function RunConsole() {
  const [repositoryPath, setRepositoryPath] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const runId = run?.run_id;

  useEffect(() => {
    const restoredRunId = new URLSearchParams(window.location.search).get("run");
    if (!restoredRunId) return;

    let cancelled = false;
    fetch(`${apiBase}/runs/${restoredRunId}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        return (await response.json()) as Run;
      })
      .then((restoredRun) => {
        if (!cancelled) {
          setRepositoryPath(restoredRun.repository_path);
          setRun(restoredRun);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(
            caught instanceof Error ? caught.message : "Unable to restore run"
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!runId) return;

    const stream = new EventSource(`${apiBase}/runs/${runId}/events/stream?after=0`);

    stream.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent;
      setError(null);
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
    };

    stream.onerror = () => {
      setError("The event stream disconnected. The browser will retry.");
    };

    return () => stream.close();
  }, [runId]);

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setEvents([]);

    try {
      const response = await fetch(`${apiBase}/runs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ repository_path: repositoryPath })
      });
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      const createdRun = (await response.json()) as Run;
      window.history.replaceState(
        null,
        "",
        `?run=${encodeURIComponent(createdRun.run_id)}`
      );
      setRun(createdRun);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create run");
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelRun() {
    if (!run) return;
    const response = await fetch(`${apiBase}/runs/${run.run_id}/cancel`, {
      method: "POST"
    });
    if (!response.ok) setError(`Cancellation returned ${response.status}`);
  }

  return (
    <section className="console" aria-label="Forge run console">
      <form onSubmit={createRun} className="run-form">
        <label htmlFor="repository-path">Local repository path</label>
        <div className="form-row">
          <input
            id="repository-path"
            value={repositoryPath}
            onChange={(event) => setRepositoryPath(event.target.value)}
            placeholder="C:\\projects\\sample-repository"
            required
          />
          <button disabled={submitting} type="submit">
            {submitting ? "Creating..." : "Create run"}
          </button>
        </div>
      </form>

      {error ? <p className="error">{error}</p> : null}

      {run ? (
        <div className="run-summary">
          <div>
            <span className="label">Run</span>
            <code>{run.run_id}</code>
          </div>
          <div>
            <span className="label">State</span>
            <strong>{run.state}</strong>
          </div>
          <button className="secondary" onClick={cancelRun} type="button">
            Cancel
          </button>
        </div>
      ) : null}

      <ol className="timeline">
        {events.map((event) => (
          <li key={event.event_id}>
            <span className="sequence">{event.sequence}</span>
            <div>
              <strong>{event.event_type}</strong>
              <p>{JSON.stringify(event.payload)}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
