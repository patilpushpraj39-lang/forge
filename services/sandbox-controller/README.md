# Forge Sandbox Controller

The sandbox controller is a separate trust boundary. It exposes bounded create,
execute, and destroy operations without exposing filesystem paths or process
handles to the workflow worker. Patch, diff, and artifact operations follow in
Milestone 3.

The first Python backend copies a repository into a disposable local workspace
and enforces timeout and cancellation. It proves the protocol but is not a host
security boundary. The stabilized concurrent controller is expected to be Go
with a container or microVM backend.
