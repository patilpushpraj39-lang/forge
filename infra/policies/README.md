# Execution Policies

Machine-readable command, filesystem, network, resource, artifact, and
retention policies live here. Policy decisions are enforced by the control
plane rather than delegated to model prompts.

[`sandbox-v1.json`](sandbox-v1.json) records the first Docker execution policy.
Unit tests ensure its resource values match the controller defaults and that the
generated runtime command contains every required isolation flag.
