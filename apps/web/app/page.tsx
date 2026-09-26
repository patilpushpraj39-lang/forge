import { RunConsole } from "./run-console";

export default function HomePage() {
  return (
    <main>
      <header className="hero">
        <p className="eyebrow">Forge milestone 6</p>
        <h1>Engineer, verify, approve.</h1>
        <p className="lede">
          Give Forge one focused task, inspect the independently verified
          patch, then approve the exact evidence before a pull request exists.
        </p>
      </header>
      <RunConsole />
    </main>
  );
}
