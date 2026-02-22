# Trade Store API

A production-grade REST API for storing and managing financial trades, built with FastAPI, Kafka back-pressure, and PostgreSQL.

---

## Architecture Overview

```
Client → FastAPI (validate) → DynamoDB (PENDING) + Kafka (trades-inbound)
                → Consumer Worker → PostgreSQL (trade store)
                ← GET /api/v1/requests/{request_id} (RequestStatusResponse)
```

- **FastAPI** handles ingestion, runs validation synchronously (fast-fail 400 on rejection), writes a PENDING request to DynamoDB, then publishes valid trades to Kafka.
- **Kafka** acts as a back-pressure buffer; the consumer updates DynamoDB with the final outcome (SUCCESS/FAILED) after writing to PostgreSQL.
- **RequestStatusResponse** is exposed via `GET /api/v1/requests/{request_id}` so clients can poll for the final status of a request.

## Design Rationale

### Why a relational database (PostgreSQL) for the trade store

The trade store uses a **SQL database (PostgreSQL)** so that future requirements—such as joins across trades, counterparties, books, or reporting tables—can be met without a disruptive database migration. Introducing a relational model later would require a large-scale migration from a document or key–value store; starting with SQL avoids that risk and keeps the schema evolvable via Alembic.

### Why asynchronous write path and Kafka

The target throughput (e.g. **1000 TPS**) makes it undesirable to write directly to the relational database on the request path: that would couple latency and availability of the API to the database and make scaling harder. By publishing accepted trades to **Kafka**, the API responds quickly and Kafka provides **back-pressure**: if the consumer cannot keep up, Kafka buffers messages and the system degrades gracefully under load rather than overloading the database.

### Why DynamoDB for request lifecycle

Trade persistence is handled **asynchronously** by a consumer. To track each request from acceptance to final outcome, the system needs a store that is fast, scalable, and operationally simple. **DynamoDB** is used for request lifecycle (PENDING → SUCCESS/FAILED) because it is a managed NoSQL service with built-in scaling and high availability, reducing operational overhead while supporting high write and read throughput for status updates and status API lookups.

### Why a RequestStatusResponse API

Because the outcome of a trade (success or failure) is determined asynchronously by the consumer, clients need a way to learn the **final status** of a request. The **RequestStatusResponse** REST API (`GET /api/v1/requests/{request_id}`) exposes request status, trade identifiers, and optional failure reason, so users can poll until the request is no longer PENDING.

### Additional architecture considerations

- **Separation of concerns**: Validation and ingestion live in the API; persistence and business rules live in the consumer, so each component can scale and be deployed independently.
- **Resilience**: Temporary failures on the API path (e.g. DynamoDB or Kafka unavailable) are surfaced as HTTP 503 with `Retry-After`, so clients can retry without losing the request intent.
- **Idempotency and ordering**: Trade identity is `(trade_id, version)`; the consumer applies upsert semantics so duplicate or out-of-order messages do not corrupt the trade store.

---

## Technology Stack

| Concern | Choice |
|---|---|
| Framework | FastAPI |
| Trade store | PostgreSQL 16 / SQLite (tests) |
| Request lifecycle | DynamoDB |
| ORM | SQLAlchemy 2.x + Alembic |
| Message broker | Apache Kafka (KRaft) |
| Kafka client | aiokafka |
| Testing | pytest + pytest-asyncio + httpx |
| Logging | structlog (JSON in prod) |
| Vulnerability scan | pip-audit |
| Containerisation | Docker + Docker Compose |
| Cloud | AWS ECS Fargate + RDS + MSK + DynamoDB |
| IaC | AWS CDK |

---

## Running Locally

### Prerequisites
- Docker and Docker Compose

```bash
# Start all services (PostgreSQL, Kafka, API, Consumer)
docker-compose up --build

# Run database migrations
docker-compose exec api alembic upgrade head
```

API available at: http://localhost:8000
Swagger docs at: http://localhost:8000/docs

---

## API Endpoints

| Method | Path | Response | Description |
|---|---|---|---|
| `POST` | `/api/v1/trades` | 202 | Validate, write PENDING to DynamoDB, publish to Kafka |
| `GET` | `/api/v1/requests/{request_id}` | 200 | Request status (PENDING / SUCCESS / FAILED) — RequestStatusResponse |
| `GET` | `/api/v1/trades` | 200 | List all trades |
| `GET` | `/api/v1/trades/{trade_id}` | 200 | Get all versions of a trade |
| `GET` | `/api/v1/health` | 200 | Health check (API + DB + Kafka) |

### Example Request

```bash
curl -X POST http://localhost:8000/api/v1/trades \
  -H "Content-Type: application/json" \
  -d '{
    "trade_id": "T1",
    "version": 1,
    "counterparty_id": "CP-1",
    "book_id": "B1",
    "maturity_date": "2026-05-20",
    "created_date": "2024-01-15"
  }'
```

---

## Validation Rules

1. **Maturity date**: trades with `maturity_date < today` are rejected with HTTP 400.
2. **Version**: trades with a version lower than the current stored max version are rejected with HTTP 400. Same version replaces the existing record (upsert). Higher version is inserted.
3. **Expiry**: computed dynamically — `expired = maturity_date < CURRENT_DATE`.

---

## Running Tests

```bash
pip install -r requirements-dev.txt

# Unit tests (no external services needed)
pytest tests/unit --cov=app --cov-report=term-missing

# Integration tests (needs PostgreSQL running)
pytest tests/integration

# All tests with coverage
pytest --cov=app --cov-fail-under=85
```

---

## Testing

1. `tests/unit/test_validators.py` → `app/validators/`
2. `tests/unit/test_trade_repository.py` → `app/repositories/`
3. `tests/unit/test_trade_service.py` → `app/services/`
4. `tests/unit/test_kafka_consumer.py` → `app/kafka/consumer.py`
5. `tests/integration/test_trade_api.py` → `app/routers/`

---

## CI/CD Pipeline

Two GitHub Actions workflows:

- **`ci.yml`**: lint (black, flake8, isort) → unit tests (≥85% coverage) → integration tests → OSS vulnerability scan (`pip-audit`)
- **`deploy.yml`**: ECR push → staging deploy → smoke test → manual approval → prod deploy

Branch strategy:
- `staging` → staging auto-deploy
- `main` → prod deploy with manual approval gate

---

## Deployment (AWS)

| Resource | Staging | Production |
|---|---|---|
| Compute | ECS Fargate (0.25 vCPU / 512 MB) | ECS Fargate (1 vCPU / 2 GB), auto-scaling |
| Database | RDS db.t3.micro, single-AZ | RDS db.t3.medium, Multi-AZ |
| Kafka | MSK kafka.t3.small, 2 AZs | MSK kafka.m5.large, 3 AZs |

See [infra/](infra/) for CDK app and stack definitions.

---

## Observability

- **Structured logging**: JSON via `structlog`, with `correlation_id` on every request.
- **Metrics**: Prometheus endpoint via `prometheus-fastapi-instrumentator`.
- **Recommended stack**: Prometheus + Grafana (metrics), Loki (logs), OpenTelemetry (tracing).

---

## Todo

Planned improvements:

1. **API Gateway** — Introduce an API Gateway (e.g. AWS API Gateway) in front of the API so that clients depend on a stable contract and endpoint. Backend topology (ALB, ECS, service split) can then change without impacting clients.
2. **Authentication and authorisation** — Add AuthN and AuthZ for public-facing APIs using Amazon Cognito (e.g. Cognito User Pools or JWT authoriser with API Gateway) so that only authenticated and authorised callers can access trade and request endpoints.
3. **DynamoDB TTL** — Enable TTL on the DynamoDB table (e.g. for request/audit records) to expire old items automatically and reduce storage cost.
4. **Hotfix workflow** — Add a GitHub Actions workflow that can be triggered manually (workflow_dispatch) with a branch selector to deploy a chosen branch (e.g. for hotfixes without merging to main/staging first).
5. **Create UML diagram** -  Add a UML diagram (e.g. component or deployment diagram for the infra/system) to document architecture.
6. **Introduce rate limiting** - Required: Business Input.
7. **Only support HTTPS** - Remove support for HTTP when certification is not supplied.
8. **Tighten the egress policy** - Right now all outbound traffic is allowed.
9. **IAM based RDS auth** - Right now it is password based.


