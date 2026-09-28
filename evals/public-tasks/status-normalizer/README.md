# Status normalizer public smoke task

This retired, public fixture powers Forge's zero-cost local demo. The starting
implementation intentionally returns status input unchanged, while the public
test requires trimming surrounding whitespace and normalizing case.

The demo uses a deterministic scripted runtime—not a language model—to exercise
the real sandbox, tool policy, patch artifact, independent evaluator, durable
events, and approval boundary. Results from this fixture are product-flow
evidence only and must not be presented as model-quality or benchmark evidence.
