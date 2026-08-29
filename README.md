# Distributed File Storage System

A production-oriented, Google Drive-like distributed file storage platform demonstrating backend engineering, distributed systems, cloud architecture, DevOps, reliability, security, and testing.

## Planned capabilities
JWT authentication, configurable user quotas, file metadata, chunked/resumable uploads, SHA-256 integrity validation, versioning, secure sharing, audit logs, content deduplication, Redis caching/coordination, pluggable local/S3 storage, storage-node replication, failure detection/recovery, Docker, Kubernetes, load balancing, CI/CD, and unit/API/integration/concurrency/failure-recovery/E2E testing.

## Architecture
Client -> Load Balancer -> FastAPI -> Services -> PostgreSQL / Redis -> Storage Manager -> Storage Nodes or S3.

PostgreSQL is the source of truth for metadata. Binary data is handled by the storage abstraction.

## Local development
Requirements: Python 3.12+, Docker, Docker Compose.

```bash
cp .env.example .env
docker compose up -d postgres redis
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs
Health: http://127.0.0.1:8000/health

## Roadmap
Foundation -> database -> auth -> quotas/metadata -> storage -> chunked uploads -> versioning -> sharing -> Redis -> replication -> recovery -> deduplication -> S3 -> React UI -> testing/benchmarks -> Kubernetes -> CI/CD -> final review.

Performance and coverage numbers are targets until actually measured.
