# Architecture

Client -> Load Balancer -> FastAPI -> Service Layer -> PostgreSQL/Redis -> Storage Manager -> Local Nodes or S3.

PostgreSQL is the durable metadata source of truth. Binary data is accessed through a storage abstraction. Replication, checksums, idempotency, failure recovery, and quota enforcement are first-class design concerns.
