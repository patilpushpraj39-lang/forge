# Forge Worker

The worker advances durable run state through leased, versioned transitions.
It selects the local or Docker sandbox backend from explicit environment
configuration and records immutable snapshot artifact references in the event
timeline. Each step has a stable owner, bounded execution, and an explicit
failure reason.
