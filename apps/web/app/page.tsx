import { RunConsole } from "./run-console";

export default function HomePage() {
  return (
    <main>
      <header className="hero">
        <p className="eyebrow">Forge milestone 1</p>
        <h1>Durable run console</h1>
        <p className="lede">
          Create one bounded repository-inspection run and watch persisted events
          arrive from the API.
        </p>
      </header>
      <RunConsole />
    </main>
  );
}

