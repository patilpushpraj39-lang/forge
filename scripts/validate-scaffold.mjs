import { access, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const requiredFiles = [
  "README.md",
  "LICENSE",
  "CONTRIBUTING.md",
  "SECURITY.md",
  "package.json",
  "pnpm-workspace.yaml",
  "pyproject.toml",
  "docs/product-contract.md",
  "docs/security/threat-model.md",
  "docs/adr/0001-application-owned-orchestration.md",
  "docs/adr/0002-postgresql-source-of-truth.md",
  "docs/adr/0003-separate-sandbox-control-boundary.md",
  "docs/adr/0004-provider-neutral-model-runtime.md",
  "packages/contracts/run-event.schema.json",
  "evals/public-tasks/README.md",
  "apps/web/app/demo/page.tsx",
  "apps/web/app/demo/offline-demo.tsx",
  "evals/public-tasks/status-normalizer/status.py",
  "evals/public-tasks/status-normalizer/test_status.py"
];

const failures = [];

for (const relativePath of requiredFiles) {
  try {
    await access(resolve(repositoryRoot, relativePath));
  } catch {
    failures.push(`missing required file: ${relativePath}`);
  }
}

try {
  const schemaText = await readFile(
    resolve(repositoryRoot, "packages/contracts/run-event.schema.json"),
    "utf8"
  );
  const schema = JSON.parse(schemaText);
  const expected = [
    "event_id",
    "run_id",
    "sequence",
    "schema_version",
    "event_type",
    "occurred_at",
    "actor",
    "payload"
  ];
  for (const property of expected) {
    if (!schema.required?.includes(property)) {
      failures.push(`run event schema does not require ${property}`);
    }
  }
  if (schema.additionalProperties !== false) {
    failures.push("run event schema must reject unknown top-level properties");
  }
} catch (error) {
  failures.push(`run event schema is invalid JSON: ${error.message}`);
}

if (failures.length > 0) {
  console.error("Forge scaffold validation failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(`Forge scaffold validation passed (${requiredFiles.length} required files).`);
