## Problem statement

The problem requires building a high-throughput REST API that ingests trade records, validates them according to defined business rules, persists them in a durable store, and allows clients to track the ingestion outcome.

| Trade Id | Version | Counter-Party Id | Book-Id | Maturity Date | Created Date | Expired |
| -------- | ------- | ---------------- | ------- | ------------- | ------------ | ------- |
| T1       | 1       | CP-1             | B1      | 20/05/2020    | <today>      | N       |
| T2       | 2       | CP-2             | B1      | 20/05/2021    | <today>      | N       |
| T2       | 1       | CP-1             | B1      | 20/05/2021    | 14/03/2015   | N       |
| T3       | 3       | CP-3             | B2      | 20/05/2014    | <today>      | Y       |

Business validation rules:

Trades with a lower version than stored must be rejected.

Trades with the same version should replace the existing record.

Trades with a maturity date before today are rejected.

Trades whose maturity dates are in the past should automatically be marked expired.

---

## Functional Requirement

1. The system shall provide an interface for users to submit requests to create new trade recordsm (POST /api/v1/trades)

2. The system shall provide an interface for users to submit requests to modify existing trade records (POST /api/v1/trades).

3. The system shall provide users with the ability to query and track the processing status of their submitted trade requests (GET /api/v1/requests/{request_id}).

4. All trade creation and update requests shall be validated against defined business rules prior to persistence. Requests that fail validation shall be rejected with appropriate error responses.

## Non Functional Requirement

**Throughput Assumption**
The problem statement specifies that up to 1,000 requests may be received by a store; however, it does not define the associated time unit. For design and capacity planning purposes, the system shall assume a target throughput of 1,000 requests per second, unless otherwise clarified.

**Availability and Latency**
The system shall ensure high availability for accepting user requests. The response time for acknowledging trade requests shall not exceed 200 milliseconds under normal operating conditions.

**Consistency**
The system shall enforce strong data consistency for trade creation and update operations to ensure data integrity within the database.

**Extensibility and Scalability**
The system architecture shall be modular and extensible, enabling future functional enhancements or scaling requirements with minimal architectural changes or large-scale migrations.

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

For tracking the request lifecycle (from acceptance to final outcome), the system requires a fast, scalable, and operationally simple datastore. While several highly scalable NoSQL alternatives such as Cassandra or Redis could fulfil this requirement, DynamoDB was selected based on the following pragmatic considerations:
1. The implementation targeted a realistic proof of concept with a fully operational CI/CD workflow on cloud infrastructure, not just local development testing.
2. I am using AWS as primary cloud environment for this project, enabling rapid provisioning of managed services with minimal setup effort.
3. DynamoDB, as a fully managed NoSQL database, provides automatic scaling, high availability, and predictable performance without the operational overhead of managing clusters or instances.

Unlike in-memory datastores such as Redis, DynamoDB persists data durably. Because the request lifecycle must be reliably queryable over time, data loss is not acceptable, making a persistent store essential.

In this architecture, trade persistence is handled asynchronously by a consumer component. DynamoDB is used specifically for request status tracking (e.g., PENDING → SUCCESS/FAILED) because it supports high write/read throughput for status updates and lookups, while minimizing operational complexity in a production-oriented environment.

### Why a RequestStatusResponse API

Because the outcome of a trade (success or failure) is determined asynchronously by the consumer, clients need a way to learn the **final status** of a request. The **RequestStatusResponse** REST API (`GET /api/v1/requests/{request_id}`) exposes request status, trade identifiers, and optional failure reason, so users can poll until the request is no longer PENDING.

---

## API Endpoints

| Method | Path | Response | Description |
|---|---|---|---|
| `POST` | `/api/v1/trades` | 202 | Validate, write PENDING to DynamoDB, publish to Kafka |
| `GET` | `/api/v1/requests/{request_id}` | 200 | Request status (PENDING / SUCCESS / FAILED) — RequestStatusResponse |
| `GET` | `/api/v1/trades` | 200 | List all trades |
| `GET` | `/api/v1/trades/{trade_id}` | 200 | Get all versions of a trade |
| `GET` | `/api/v1/health` | 200 | Health check (API + DB + Kafka) |

---

## Code Walkthrough

1. API Layer

Implemented using FastAPI in main.py and routes/trade.py.

Defines REST endpoints for:

Trade ingestion (POST /api/v1/trades)
Trade listing (GET /api/v1/trades)
Trade lookup by ID (GET /api/v1/trades/{trade_id})
Request status retrieval (GET /api/v1/requests/{request_id})

Uses Pydantic models for request validation ensuring required fields and correct data types.

2. Domain & Validation

Business validation logic resides in service modules.

Before persisting business object to DB:
Verifies maturity date is not in the past,
Ensures versioning rules (reject lower version, replace same version),
Assigns and tracks request status.

3. Asynchronous Processing

Valid trades are published to Kafka.
The producer encapsulates message creation and publication logic.
This decouples API response from database writes, improving performance.

4. Consumer Logic

A Kafka consumer listens to trade events.

For each message:

Applies final persistence to PostgreSQL via the repository/ORM layer.
Updates the request status in DynamoDB (e.g., SUCCESS, FAILED).
Handles expiry marking based on maturity.

5. Persistence Layers

DynamoDB tables store request lifecycle metadata (status, timestamps).
PostgreSQL stores the canonical trade records with versioning.
SQLAlchemy and Alembic handle migrations and model definitions.

6. Testing

Tests are grouped into:

Unit tests for validation logic and internal functions.
Integration tests for API route behavior.
Async tests use pytest-asyncio and httpx.
Coverage thresholds are enforced in CI.

1. `tests/unit/test_validators.py` → `app/validators/`
2. `tests/unit/test_trade_repository.py` → `app/repositories/`
3. `tests/unit/test_trade_service.py` → `app/services/`
4. `tests/unit/test_kafka_consumer.py` → `app/kafka/consumer.py`
5. `tests/integration/test_trade_api.py` → `app/routers/`

7. CI/CD Integration

GitHub Actions workflows automate:

Test execution,
Lint checks,
Vulnerability scans (pip-audit),
Build and deployment steps.
- **`ci.yml`**: lint (black, flake8, isort) → unit tests (≥85% coverage) → integration tests → OSS vulnerability scan (`pip-audit`)
- **`deploy.yml`**: ECR push → staging deploy → smoke test → manual approval → prod deploy

Branch strategy:
- `staging` → staging auto-deploy
- `main` → prod deploy with manual approval gate


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
- **Cloudwatch metric**: Cloudwatch metrics are enabled at load balancer level to for request count, 4xx error count, 5xx error count, latency. 
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

### Example requests

#### POST /api/v1/trades

Response (202) includes `request_id`; use it with GET /api/v1/requests/{request_id} to poll status.

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

#### GET /api/v1/requests/{request_id}

Replace `<request_id>` with the UUID returned in the POST /api/v1/trades response.

```bash
curl -s http://localhost:8000/api/v1/requests/<request_id>
```

#### GET /api/v1/trades

```bash
curl -s http://localhost:8000/api/v1/trades
```

#### GET /api/v1/trades/{trade_id}

```bash
curl -s http://localhost:8000/api/v1/trades/T1
```

#### GET /api/v1/health

Response: `{"status":"ok","db":"ok","kafka":"ok"}` when healthy, or `"status":"degraded"` when DB or Kafka is down.

```bash
curl -s http://localhost:8000/api/v1/health
```

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

## Best Practices and Design Patterns Used

1. Separation of Concerns:
The codebase is cleanly divided into API routes, service/validation logic, messaging (Kafka producer/consumer), and persistence layers (PostgreSQL/DynamoDB). This modular organization improves readability, enables isolated testing, and makes future extensions safer and easier.

2. Controller-Service-Repository Pattern:
REST controllers handle request/response mapping, service classes encapsulate business rules, and repositories abstract database operations. This pattern promotes single responsibility and decouples business logic from transport and storage concerns.

3. Decoupled Architecture:
Trade persistence is decoupled from API ingestion using Kafka, allowing the system to scale and absorb load spikes without tying API latency to database performance. Asynchronous consumes are handled reliably, following producer/consumer patterns.

4. Schema Validation with Pydantic — Input validation is enforced at the API boundary using Pydantic models, catching invalid data early and providing clear error feedback. This practice reduces downstream errors and improves API contract reliability.

5. Configuration-Driven Design — Environment variables and configuration files drive deployment specifics, enabling consistency between environments (local, staging, production) and reducing hard-coded values.

6. Dependency Isolation and Virtual Environments — The repository uses explicit dependency management and environment isolation, avoiding version conflicts and making builds reproducible.

## Code Quality Maintainance

1. Test-Driven Development (TDD): Tests were written before implementation to drive design and prevent regressions. Critical business logic and API behaviors are backed by comprehensive unit and integration tests.

2. Continuous Integration Gates: The GitHub Actions pipeline runs linting, tests, and coverage checks on every commit and pull request. The build fails if tests fail, coverage drops, or critical vulnerabilities are detected.

3. Static Analysis and Formatting: Tools such as black, flake8, and isort enforce consistent code style and catch common errors before merge. This reduces stylistic drift and improves readability.

4. Layered Architecture: Clear separation of API, service, consumer, and persistence layers improves modularity and allows isolated testing. Each layer has a well-defined responsibility, reducing coupling and simplifying maintenance.

5. Strict Validation Models: Pydantic schemas validate input at the API boundary, preventing invalid data from propagating into business logic. Type hints provide additional safety and IDE assistance during development.

6. Structured Logging: Consistent and structured logging is used throughout, aiding in debugging and traceability. Errors are captured in a predictable format for analysis.

7. Dependency Management: Dependencies are version-pinned and scanned for vulnerabilities in CI, reducing the risk of security issues or unexpected upgrades. Regular scanning ensures known issues are caught early.

## What Could Have Been Done Better

1. Enhanced Observability and Tracing

Integrate distributed tracing using OpenTelemetry, capturing end-to-end traces across API, Kafka, consumer, and database layers.

Build dashboards in Grafana for service performance, error rates, throughput, and trade processing latency.

2. Advanced Monitoring and Alerting

Deploy Prometheus or a managed alternative (e.g., Amazon Managed Service for Prometheus) to collect detailed metrics from FastAPI, Kafka, and consumers.

Define SLO/SLI based alerts (latency, errors, throughput) integrated with on-call systems (PagerDuty, Opsgenie).

Implement automated anomaly detection for early warning of degraded performance.

3. Security Hardening

Add API authentication and authorization using standards like OAuth 2.0 / JWT, or integrate with identity providers (AWS Cognito, Auth0).

Tighen the security group to allow specific egress and ingress.

Introduce rate limiting based considering the business requirement.

Remove password base access to IAM base access for RDS. 

4. Resilience

Implement circuit breakers, retries, and fallback patterns to improve fault tolerance.

Support multi-region deployments for disaster recovery and regional performance.

5. Performance Optimization
Implement batching strategies for Kafka consumers to optimize database writes.

6. Immutable Deployments
Support immutable deployments via blue/green or canary releases to minimize downtime and risk.

7. Comprehensive End-to-End Testing
Implement performance testing using tools like k6, Locust, or Gatling to validate 1,000+ RPS sustained loads.

Add chaos tests (Chaos Monkey) to validate system resilience under failure scenarios.

8. Cost optimization

Enable TTL on the DynamoDB table (e.g. for request/audit records) to expire old items automatically and reduce storage cost.

9. Improve deployment pipeline to support emergent changes

Add a GitHub Actions workflow that can be triggered manually (workflow_dispatch) with a branch selector to deploy a chosen branch (e.g. for hotfixes without merging to main/staging first)

## UML diagrams

Source PlantUML diagrams are in [`uml_diagrams/`](uml_diagrams/):

- **[sequence_flow.puml](uml_diagrams/sequence_flow.puml)** — request/response and Kafka flow
- **[class_diagram.puml](uml_diagrams/class_diagram.puml)** — class structure
- **[architecture.puml](uml_diagrams/architecture.puml)** — system architecture
