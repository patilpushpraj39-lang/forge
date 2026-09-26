import { RunConsole } from "./run-console";

export default function HomePage() {
  return (
    <main>
      <header className="hero">
        <p className="eyebrow">Forge milestone 4</p>
        <h1>Bounded engineering run</h1>
        <p className="lede">
          Give Forge one focused engineering task, preserve its limits, and
          watch every durable action arrive from the API.
        </p>
      </header>
      <RunConsole />
    </main>
  );
}
