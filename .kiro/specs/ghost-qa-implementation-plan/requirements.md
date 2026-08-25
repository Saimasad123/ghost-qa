# Requirements Document

## Introduction

Ghost QA is an AI-powered QA engineer that integrates with GitHub pull request
workflows, analyzes code diffs using Claude/Grok AI, generates structured test
cases, orchestrates execution through UiPath Test Cloud, calculates risk,
posts results back to GitHub, and can self-heal failing tests.

The core demo-mode pipeline is fully functional end-to-end. This document
captures the remaining work required to bring Ghost QA from demo-ready to
production-ready. Requirements are organized into five phases:

1. **Phase 1 – Security & Auth** — API authentication/authorization
2. **Phase 2 – Real UiPath Execution** — Full Test Cloud integration
3. **Phase 3 – Database Migrations** — Alembic migration system
4. **Phase 4 – Frontend & UX** — Dashboard, report page, and pagination
5. **Phase 5 – Operational Hardening** — Rate limiting, deployment, SLA timers, testing quality

Each requirement has an explicit acceptance criterion set following EARS
patterns so completion can be verified objectively.

---

## Glossary

- **API**: The Ghost QA FastAPI application and its HTTP endpoints.
- **Auth_Service**: The component responsible for issuing and validating JWT tokens (currently a stub in `src/auth.py`).
- **Alembic**: The SQLAlchemy migration framework listed in `requirements.txt` but not yet configured.
- **Dashboard**: The HTML/JS page served at `/dashboard` and its backing API endpoints.
- **Executor**: The `UiPathExecutor` class in `app/services/executor.py` responsible for running tests on UiPath Test Cloud.
- **Migration**: A numbered Alembic script that evolves the database schema without data loss.
- **Operator**: A human user who interacts with the Ghost QA API (approves tests, views reports).
- **PipelineRun**: A Ghost QA record representing one complete cycle triggered by a GitHub PR event.
- **Rate_Limiter**: The middleware or decorator that limits the number of requests per IP/token per time window.
- **Robot**: A UiPath automation robot registered in UiPath Orchestrator.
- **SLA_Timer**: A background task that escalates or cancels pending approvals after a configured duration.
- **System**: The Ghost QA application as a whole.
- **Test_Cloud**: UiPath Test Cloud / Test Manager service where XAML test assets are uploaded and executed.
- **Test_Manager**: The UiPath Test Manager component used to store test cases and test sets.
- **XAML_Generator**: The `app/services/xaml_generator.py` service that converts JSON test cases into UiPath Studio `.xaml` format.

---

## Requirements

### Requirement 1: JWT Authentication

**User Story:** As an operator, I want API endpoints to require a valid JWT
token, so that unauthenticated callers cannot approve tests, reject heals, or
access sensitive pipeline data.

#### Acceptance Criteria

1. WHEN a caller sends a `POST /auth/login` request with a valid email and password pair, THE Auth_Service SHALL return a JSON response containing a signed JWT `token` field and an `expires_in` field (seconds until expiry).
2. IF a caller sends `POST /auth/login` with an incorrect email or password, THEN THE Auth_Service SHALL return `401 Unauthorized` without disclosing which field was incorrect.
3. WHEN a request is received on any `/api/` endpoint without a valid `Authorization: Bearer <token>` header, THE API SHALL return `401 Unauthorized` and reject the request without executing the handler.
4. WHEN a request is received with a JWT whose cryptographic signature does not verify against `SECRET_KEY`, THE API SHALL return `401 Unauthorized`.
5. WHEN a request is received with a JWT whose `exp` claim is in the past, THE API SHALL return `401 Unauthorized`.
6. WHEN a request is received with a valid, unexpired JWT, THE API SHALL forward it to the target handler and return a non-401 HTTP response.
7. THE Auth_Service SHALL sign tokens using the `SECRET_KEY` setting from `app/config.py`.
8. THE Auth_Service SHALL set token expiry using a `JWT_EXPIRY_MINUTES` environment variable (range: 15–1440 minutes / 900–86400 seconds). IF `JWT_EXPIRY_MINUTES` is outside that range, THE System SHALL reject startup with a configuration error.

---

### Requirement 2: Role-Based Access Control

**User Story:** As a system administrator, I want different operator roles to
have different access levels, so that read-only users cannot accidentally
approve or reject tests.

#### Acceptance Criteria

1. THE Auth_Service SHALL support at least two roles encoded as a `role` JWT claim with exact string values `"viewer"` and `"approver"`.
2. WHEN an authenticated `viewer` role token is used on a write endpoint (`POST /api/tests/{test_id}/approve`, `POST /api/tests/{test_id}/reject`, `POST /api/runs/{run_id}/approve`, `POST /api/heals/{heal_id}/approve`, `POST /api/heals/{heal_id}/reject`, `POST /api/heals/{heal_id}/execute`), THE API SHALL return `403 Forbidden` without executing the requested operation.
3. WHEN an authenticated `approver` role token is used on any endpoint, THE API SHALL pass the request to the endpoint handler without applying any role-based restriction.
4. WHEN a valid JWT token contains a missing or unrecognized `role` claim value, THE API SHALL return `403 Forbidden`.
5. IF a request carries a valid JWT token but the role is insufficient for the requested endpoint, THEN THE API SHALL return `403 Forbidden` (not `401`).

---

### Requirement 3: Real UiPath Test Cloud Execution

**User Story:** As a QA engineer, I want Ghost QA to actually execute generated
test cases on a real UiPath robot, so that test results reflect genuine
UI/API behavior rather than random simulation.

#### Acceptance Criteria

1. WHEN `DEMO_MODE` is `false` and all five UiPath credentials (`UIPATH_CLIENT_ID`, `UIPATH_CLIENT_SECRET`, `UIPATH_TENANT_NAME`, `UIPATH_ORG_ID`, `UIPATH_ENVIRONMENT_ID`) are configured, THE Executor SHALL upload the generated XAML file for a test case to Test_Manager via the UiPath API.
2. WHEN the XAML file has been uploaded, THE Executor SHALL create a test set in Test_Manager containing that test case.
3. WHEN the test set has been created, THE Executor SHALL trigger execution of the test set on the Robot associated with `UIPATH_ENVIRONMENT_ID`.
4. WHILE execution is running, THE Executor SHALL poll the UiPath API for status at an interval no greater than 10 seconds.
5. WHEN execution completes with a UiPath result of `Passed`, THE Executor SHALL store `outcome=passed`. WHEN the result is `Failed`, THE Executor SHALL store `outcome=failed`. WHEN the result is `Cancelled`, THE Executor SHALL store `outcome=failed` and `failure_type=unknown`.
6. IF the UiPath API returns an error during any step (upload, create, trigger, poll), THEN THE Executor SHALL log the error and mark the `TestResult` with `outcome=failed` and `failure_type=unknown`.
7. IF XAML generation fails before upload, THEN THE Executor SHALL log the error and mark the `TestResult` with `outcome=failed` and `failure_type=unknown` without attempting upload.
8. IF `DEMO_MODE` is `true` OR any of the five required UiPath credentials is absent, THEN THE Executor SHALL fall back to `MockExecutor` without logging a "work in progress" warning.
9. WHEN a test execution has been running for longer than `UIPATH_EXECUTION_TIMEOUT_SECONDS` (default 300), THE Executor SHALL cancel the test set and record `outcome=timed_out`.

---

### Requirement 4: XAML Quality and Validation

**User Story:** As a QA engineer, I want XAML files generated by Ghost QA to
be valid for direct import into UiPath Studio, so that I can inspect or
manually replay tests without modification.

#### Acceptance Criteria

1. WHEN XAML_Generator produces a `.xaml` file, THE XAML_Generator SHALL produce output that is well-formed XML (parseable by Python's `xml.etree.ElementTree` without raising `ParseError`).
2. THE XAML_Generator SHALL include a `xmlns:ui="http://schemas.uipath.com/workflow/activities"` namespace declaration on the root element.
3. WHEN a test case has N steps (N ≥ 1), THE XAML_Generator SHALL produce exactly N `<Sequence>` child elements within the `<FlowStep>` body.
4. WHEN a test case has 0 steps, THE XAML_Generator SHALL produce well-formed XAML containing no `<Sequence>` elements and no errors.
5. WHEN a test case with N steps is serialised to XAML and the step count is re-extracted by counting `<Sequence>` elements inside `<FlowStep>`, the extracted count SHALL equal N (round-trip count invariant).

---

### Requirement 5: Alembic Database Migrations

**User Story:** As a developer, I want schema changes to be managed through
versioned migration scripts, so that I can evolve the database without losing
data or manually dropping tables.

#### Acceptance Criteria

1. THE System SHALL include an `alembic.ini` file and an `alembic/` directory with a configured `env.py` that reads `DATABASE_URL` from the application settings.
2. WHEN `alembic upgrade head` is run against an empty database, THE System SHALL create all six tables (`organisations`, `repositories`, `pipeline_runs`, `test_cases`, `test_results`, `heal_attempts`) and all five composite indexes defined in `app/models.py`.
3. WHEN `alembic upgrade head` is run against an already-migrated database, THE System SHALL emit no DDL statements and exit with code 0.
4. WHEN a developer adds a new column to a model in `app/models.py` and generates a new migration, THE System SHALL produce a migration script that contains an `add_column` operation and no `drop_table` operation.
5. THE System SHALL include a baseline migration script (`001_initial_schema.py`) that captures the schema of all six tables and five composite indexes as defined in `app/models.py` at the time the migration is first generated.

---

### Requirement 6: Pagination on List Endpoints

**User Story:** As a dashboard consumer, I want list endpoints to support
cursor or offset pagination, so that large numbers of pipeline runs do not
cause slow or truncated responses.

#### Acceptance Criteria

1. THE API's `GET /api/runs` endpoint SHALL accept `page` (integer ≥ 1, default 1) and `page_size` (integer 1–100, default 20) query parameters.
2. WHEN `GET /api/runs` is called with `page=2&page_size=10`, THE API SHALL return the runs at offset 10–19 (zero-indexed), ordered by `created_at` descending.
3. THE API SHALL include a `pagination` object in the response containing `total` (count of all runs in the database), `page` (the requested page number), `page_size` (the effective page size), and `has_next` (`true` if `page * page_size < total`, `false` otherwise).
4. WHEN `page_size` exceeds 100 or is less than 1, THE API SHALL return `422 Unprocessable Entity`.
5. WHEN there are no runs matching the requested page, THE API SHALL return an empty `runs` array and `has_next=false` rather than an error.

---

### Requirement 7: Dashboard Frontend Completion

**User Story:** As an operator, I want the web dashboard to display live
pipeline status, test results, and risk summaries, so that I can monitor Ghost
QA without using the raw API.

#### Acceptance Criteria

1. WHEN the dashboard page is loaded, THE Dashboard SHALL display the total number of repositories, total pipeline runs, and the count of items in the `recent_runs` array — all fetched from `GET /api/dashboard/overview`.
2. WHEN a pipeline run row is clicked in the runs table, THE Dashboard SHALL navigate to `/report/{run_id}`.
3. WHILE the dashboard page is open, THE Dashboard SHALL re-fetch `GET /api/dashboard/overview` every 30 seconds and update both the stat cards and the runs table without a full page reload.
4. WHEN a run has `status=awaiting_approval`, THE Dashboard SHALL display an "Approve All" button in the run row that calls `POST /api/runs/{run_id}/approve`; on success the button SHALL be replaced with a "Approved" label; on failure THE Dashboard SHALL display an inline error message.
5. IF the fetch of `/api/dashboard/overview` fails, THEN THE Dashboard SHALL display an error message in the page body (not only the browser console).
6. THE Dashboard SHALL be usable without JavaScript frameworks — vanilla JS or a CDN-loaded library only (no build step required).

---

### Requirement 8: Report Page Completion

**User Story:** As a developer reviewing a PR, I want to open the Ghost QA
report for a specific pipeline run and see all test outcomes, risk level, and
any heal attempts, so that I can understand what happened without reading raw
JSON.

#### Acceptance Criteria

1. WHEN `/report/{run_id}` is loaded with a valid run ID, THE Report Page SHALL display the PR number, the first 7 characters of the commit SHA, the overall risk level (one of `low`, `medium`, `high`, `critical`), and the pipeline status — all fetched from `GET /api/dashboard/runs/{run_id}/details`.
2. WHILE the fetch is in progress, THE Report Page SHALL display a loading indicator in the main content area.
3. THE Report Page SHALL display each test case with its title, `test_type` (one of `functional`, `regression`, `edge_case`, `integration`), priority, outcome (one of `passed`, `failed`, `skipped`, `timed_out`, or `pending` if null), and failure message when outcome is `failed` or `timed_out`.
4. WHEN a test case has one or more heal attempts, THE Report Page SHALL display each heal's status (`proposed`, `accepted`, `verified`, or `rejected`); the heal attempt data SHALL be included in the `GET /api/dashboard/runs/{run_id}/details` response.
5. WHEN `/report/{run_id}` is loaded with a run ID that does not exist, THE Report Page SHALL display a "Run not found" message.
6. IF the fetch of run details fails with a network error, THEN THE Report Page SHALL display an error message identifying that the load failed and a retry button that re-issues the same fetch.

---

### Requirement 9: UiPath Action Center Integration

**User Story:** As a QA lead, I want pending test approvals to be routed
through UiPath Action Center when not in demo mode, so that approvals are
tracked in UiPath alongside the test execution workflow.

#### Acceptance Criteria

1. IF `DEMO_MODE` is `false`, THEN WHEN a PipelineRun reaches `status=awaiting_approval`, THE System SHALL create an Action Center task in UiPath containing the list of pending test case titles and their risk levels.
2. WHEN an operator approves a subset of tests via the Action Center task, THE System SHALL set those `TestCase.approval_status` values to `approved` (with `approved_by="action_center"`) and advance the `PipelineRun` to `status=running` for the approved tests; unapproved tests are set to `rejected`.
3. WHEN an operator fully rejects the Action Center task, THE System SHALL set all pending `TestCase.approval_status` values to `rejected` and set the `PipelineRun` to `status=failed` with reason `operator_rejected`.
4. WHEN an Action Center task has been pending for more than 4 hours, THE SLA_Timer SHALL post a Slack notification to the `SLACK_CHANNEL` channel containing the `PipelineRun.id` and the URL of the Action Center task.
5. WHEN an Action Center task has been pending for more than 24 hours, THE SLA_Timer SHALL automatically reject all pending test cases, cancel the Action Center task, and set the `PipelineRun` to `status=failed` with reason `approval_timeout`.
6. IF `DEMO_MODE` is `true`, THEN THE System SHALL bypass Action Center and call `approve_all` with `approved_by="system"` before advancing the PipelineRun to `status=running`.
7. WHEN an Action Center task is created, THE System SHALL store the Action Center task ID in `PipelineRun.linked_issue_id` for correlation.
8. IF the Action Center task creation API call fails, THEN THE System SHALL log the error at `ERROR` level and fall back to the local approval API, leaving the `PipelineRun` at `status=awaiting_approval` for manual resolution.

---

### Requirement 10: Rate Limiting

**User Story:** As a system administrator, I want the webhook and API endpoints
to enforce rate limits, so that a misconfigured GitHub webhook or a malicious
caller cannot overwhelm the service.

#### Acceptance Criteria

1. THE API SHALL limit `POST /api/webhooks/github` to a maximum of 60 requests per fixed minute window per source IP address.
2. WHEN the rate limit is exceeded, THE API SHALL return `429 Too Many Requests` with a `Retry-After` header containing the integer number of seconds remaining until the current fixed window resets, and a JSON response body with an `error` field.
3. THE API SHALL limit all `/api/` endpoints except `POST /api/webhooks/github` to a maximum of 120 requests per fixed minute window per authenticated user token (or per source IP when unauthenticated).
4. WHEN a rate-limit violation occurs, THE System SHALL log a `WARNING` entry containing the source IP address and the endpoint path.
5. THE System SHALL include a `RATE_LIMIT_ENABLED` configuration flag in `app/config.py` with a default value of `true`. WHERE `RATE_LIMIT_ENABLED` is set to `false`, THE API SHALL skip all rate-limit checks.

---

### Requirement 11: Containerisation and Deployment

**User Story:** As a DevOps engineer, I want to deploy Ghost QA using Docker
Compose, so that the application and its dependencies can be started
reproducibly in any environment.

#### Acceptance Criteria

1. THE System SHALL include a `Dockerfile` that builds the Ghost QA application image using `python:3.11-slim` as the base image.
2. THE System SHALL include a `docker-compose.yml` that defines a `ghost-qa` service and a `postgres` service using `postgres:15`, wiring `DATABASE_URL=postgresql://ghost_qa:password@postgres:5432/ghost_qa` automatically.
3. WHEN `docker compose up` is run from the project root with valid environment variables in a `.env` file, THE System SHALL start and return HTTP 200 on `GET /` within 30 seconds.
4. THE Dockerfile SHALL execute `alembic upgrade head` as an `ENTRYPOINT` step before starting `uvicorn`, so that migrations run on every container start.
5. THE System SHALL include a `.dockerignore` file that excludes `.git`, `__pycache__`, `*.pyc`, files matching `*.db`, and the virtual environment directory (e.g. `venv/`, `.venv/`).

---

### Requirement 12: Screenshot Capture

**User Story:** As a QA engineer, I want test failure screenshots to be
attached to test results, so that I can visually inspect what went wrong
without re-running the test.

#### Acceptance Criteria

1. WHEN a UiPath test execution completes with `outcome=failed`, THE Executor SHALL attempt to retrieve a screenshot URL from the UiPath Test Cloud result payload and store it in `TestResult.screenshot_url`.
2. IF UiPath does not provide a screenshot URL in the result payload, THEN THE Executor SHALL leave `TestResult.screenshot_url` as `null` without raising an error.
3. WHEN `TestResult.screenshot_url` is non-null, THE Report Page SHALL display a thumbnail `<img>` element whose `src` is the screenshot URL and which links to the full-size screenshot on click.

---

### Requirement 13: Property-Based Tests for Core Logic

**User Story:** As a developer, I want property-based tests covering the
risk engine, AI response parser, and executor result mapping, so that edge
cases with unusual inputs are caught automatically rather than manually.

#### Acceptance Criteria

1. THE System SHALL include `hypothesis` as a dependency in `requirements.txt`.
2. THE System SHALL include a property-based test that verifies: for all non-empty lists of `TestResult` objects with any combination of `TestPriority` and `TestOutcome` values, `RiskEngine.calculate_risk()` returns a `risk_level` that is exactly one of `RiskLevel.low`, `RiskLevel.medium`, `RiskLevel.high`, or `RiskLevel.critical` (completeness invariant).
3. THE System SHALL include a property-based test that verifies: as the count of `TestResult` objects with `outcome=failed` and `priority=p0_critical` increases from 0 to N (all other inputs held constant), the `risk_level` returned by `RiskEngine` is monotonically non-decreasing in the order `low ≤ medium ≤ high ≤ critical` (monotonicity invariant).
4. THE System SHALL include a property-based test that verifies: for all JSON strings that conform to the `{"tests": [...]}` schema expected by `AIBrainService._parse_json()`, the method returns a `dict` without raising an exception (parse invariant).
5. THE System SHALL include a property-based test that verifies: for all valid `TestCaseSchema` objects, calling `.model_dump_json()` and then `TestCaseSchema.model_validate_json()` on the result produces an object equal to the original (round-trip invariant).
6. WHEN Hypothesis finds a counterexample for any of the above properties, THE test runner SHALL report the minimised failing example in the pytest output via Hypothesis shrinking.

---

### Requirement 14: Organisation Management API

**User Story:** As an administrator, I want REST endpoints to create, list, and
deactivate repositories, so that I can manage which repositories Ghost QA
monitors without touching the database directly.

#### Acceptance Criteria

1. THE API SHALL expose `GET /api/orgs` that returns a JSON array of all `Organisation` records, each containing `id`, `name`, `plan`, and `created_at` fields.
2. THE API SHALL expose `GET /api/orgs/{org_id}/repos` that returns a JSON array of all `Repository` records where `organisation_id = org_id`, each containing `id`, `full_name`, `default_branch`, `is_active`, and `created_at`.
3. THE API SHALL expose `POST /api/orgs/{org_id}/repos` that creates and returns a new `Repository` record given `full_name` (required), `default_branch` (required), and `webhook_secret` (optional) in the request body.
4. THE API SHALL expose `DELETE /api/orgs/{org_id}/repos/{repo_id}` that sets `Repository.is_active = false` and returns `204 No Content` (the record is retained in the database).
5. WHEN `DELETE /api/orgs/{org_id}/repos/{repo_id}` is called for a repository that has pipeline runs with `status` not in `{completed, failed}`, THE API SHALL return `409 Conflict` with a JSON body listing the active `run_id` values.
6. IF the `org_id` referenced in any org endpoint does not exist, THEN THE API SHALL return `404 Not Found`.
7. IF the `repo_id` referenced in any repo endpoint does not exist or does not belong to `org_id`, THEN THE API SHALL return `404 Not Found`.
