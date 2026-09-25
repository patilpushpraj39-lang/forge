# Forge Worker

The worker will advance durable run state through leased, versioned transitions. Each step will have a stable idempotency key and an explicit budget.

