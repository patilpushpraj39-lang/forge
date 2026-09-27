import { ForgeConsole } from "./auth-console";
import Link from "next/link";

export default function HomePage() {
  return (
    <main>
      <header className="hero">
        <nav className="home-nav" aria-label="Forge navigation">
          <Link href="/demo">Try free offline demo →</Link>
          <Link href="/benchmarks">Open failure analysis</Link>
        </nav>
        <p className="eyebrow">Forge milestone 6</p>
        <h1>Engineer, verify, approve.</h1>
        <p className="lede">
          Give Forge one focused task, inspect the independently verified
          patch, then approve the exact evidence before a pull request exists.
        </p>
      </header>
      <ForgeConsole />
    </main>
  );
}
