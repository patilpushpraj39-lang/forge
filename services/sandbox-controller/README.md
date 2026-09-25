# Forge Sandbox Controller

The sandbox controller is a separate trust boundary. It will expose bounded create, execute, patch, diff, artifact, and destroy operations without exposing container-runtime primitives to the workflow service.

The first prototype may be Python. The stabilized concurrent controller is expected to be Go.

