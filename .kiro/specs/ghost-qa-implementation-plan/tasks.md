# Implementation Plan: Ghost QA — Demo to Production

## Overview

Convert the 14 production-readiness requirements across 5 phases into discrete coding tasks.
Each task builds incrementally on the previous ones. All phases are largely independent, with the
single hard dependency that Phase 3 (Alembic) must complete before Phase 5 Docker packaging
because the Dockerfile entrypoint runs `alembic upgrade head`.

The implementation language is **Python** throughout (FastAPI / SQLAlchemy / Pydantic stack
already in use).

---

## Tasks

- [ ] 1. Phase 1 — Security & Auth

  - [x] 1.1 Extend `app/config.py` with auth and JWT config fields
    - Add `SECRET_KEY: str`, `JWT_EXPIRY_MINUTES: int = Field(default=60, ge=15, le=1440)`,
      and `AUTH_USERS: str` (JSON string seeding the in-memory credential store)
    - Pydantic `Field(ge=15, le=1440)` must raise `ValidationError` on startup if value is out of range
    - _Requirements: 1.7, 1.8_

  - [x] 1.2 Implement `app/services/auth.py` — AuthService
    - Implement `create_token(email, role) -> dict` returning `{"token": str, "expires_in": int}`
    - Implement `verify_token(token: str) -> dict` — return decoded payload or raise `HTTPException(401)`
    - Sign/verify with HS256 using `settings.SECRET_KEY`; set expiry from `settings.JWT_EXPIRY_MINUTES`
    - Token payload: `{"sub": email, "role": "viewer"|"approver", "exp": ..., "iat": ...}`
    - _Requirements: 1.1, 1.2, 1.7, 1.8_

  - [ ]* 1.3 Write unit tests for AuthService
    - Test `create_token` returns correct structure and fields
    - Test `verify_token` accepts a valid token, rejects a wrong-key token, rejects an expired token
    - Test that `JWT_EXPIRY_MINUTES` outside 15–1440 raises at settings load time
    - _Requirements: 1.1, 1.4, 1.5, 1.8_

  - [x] 1.4 Implement `app/api/auth.py` — login router
    - `POST /auth/login`: body `{"email", "password"}`; verify against `AUTH_USERS` in-memory dict
    - 200 → `{"token": str, "expires_in": int}`; 401 → `{"detail": "Invalid credentials"}` (no field disclosure)
    - Mount router on `app/main.py` at prefix `/auth`
    - _Requirements: 1.1, 1.2_

  - [x] 1.5 Implement `app/middleware/auth.py` — JWT middleware
    - `BaseHTTPMiddleware` subclass; skip paths not starting with `/api/`
    - Read `Authorization: Bearer <token>`, call `auth_service.verify_token()`
    - On success: attach decoded payload to `request.state.user`
    - On failure: return `JSONResponse({"detail": "..."}, status_code=401)` without calling the handler
    - Register middleware in `app/main.py`
    - _Requirements: 1.3, 1.4, 1.5, 1.6_

  - [x] 1.6 Implement `app/dependencies.py` — RBAC dependency
    - `get_current_user(request)` — reads `request.state.user`; raises 401 if missing
    - `require_approver(user)` — raises `HTTPException(403)` if `user["role"] != "approver"` or role is unrecognised
    - Apply `require_approver` to: `POST /api/tests/{id}/approve`, `POST /api/tests/{id}/reject`,
      `POST /api/runs/{id}/approve`, `POST /api/heals/{id}/approve`, `POST /api/heals/{id}/reject`,
      `POST /api/heals/{id}/execute`
    - Wire `get_current_user` as a dependency on all remaining `/api/` routes for auth enforcement
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 1.7 Write unit tests for JWT middleware and RBAC (`tests/test_auth.py`)
    - Test middleware bypass on public routes (e.g., `/`, `POST /auth/login`)
    - Test 401 returned for missing token on `/api/runs`
    - Test 401 for invalid/expired tokens
    - Test 403 returned for viewer role on each of the 6 write endpoints
    - Test 403 for missing or unrecognised `role` claim
    - Test approver role passes through successfully
    - Override `get_current_user` dependency in existing test suite to avoid breaking existing tests
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 2.2, 2.3, 2.4, 2.5_

- [ ] 2. Phase 2 — Real UiPath Execution & XAML Quality

  - [x] 2.1 Rewrite `app/services/xaml_generator.py` using `xml.etree.ElementTree`
    - Remove the existing string-template approach
    - Build the XML tree programmatically: root `<Activity>` with `xmlns:ui="http://schemas.uipath.com/workflow/activities"` and other required namespaces
    - For each step, append one `<ui:Sequence DisplayName="Step N: {action}">` as a direct child of `<ui:FlowStep>`
    - Zero-step test cases produce a `<ui:FlowStep>` with no `<ui:Sequence>` children
    - Serialise with `ET.tostring(root, encoding="unicode", xml_declaration=True)`
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 2.2 Write property test for XAML well-formedness (Property 4)
    - **Property 4: XAML is always well-formed XML**
    - **Validates: Requirements 4.1, 4.2**
    - In `tests/test_pbt.py`, using `@given(test_case=test_case_strategy())` (strategy in `tests/strategies.py`)
    - Assert `ET.fromstring(xaml)` does not raise `ParseError`
    - Use `@settings(max_examples=200)`

  - [ ]* 2.3 Write property test for XAML step count round-trip (Property 5)
    - **Property 5: XAML step count round-trip invariant**
    - **Validates: Requirements 4.3, 4.4, 4.5**
    - In `tests/test_pbt.py`, using `@given(steps=lists(step_strategy(), min_size=0, max_size=50))`
    - Parse the output XAML and count `<ui:Sequence>` children of `<ui:FlowStep>`; assert count == `len(steps)`

  - [ ]* 2.4 Write unit tests for `XamlGenerator` (`tests/test_xaml_generator.py`)
    - Test 0 steps → well-formed, 0 `<Sequence>` elements
    - Test 1 step → well-formed, 1 `<Sequence>` element
    - Test N steps → well-formed, N `<Sequence>` elements
    - Test `xmlns:ui` namespace declaration is present on root
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 2.5 Add new config keys to `app/config.py` for UiPath execution
    - `UIPATH_EXECUTION_TIMEOUT_SECONDS: int = 300`
    - `UIPATH_TEST_MANAGER_BASE: str = "https://cloud.uipath.com"`
    - _Requirements: 3.4, 3.9_

  - [x] 2.6 Complete `UiPathExecutor.execute_test` in `app/services/executor.py`
    - Implement Step 2: upload XAML via `POST .../testcases` (multipart/form-data); store returned `uipath_test_id`
    - Implement Step 3: create test set via `POST .../testsets` with `{"Name": "GhostQA-{run_id[:8]}", "TestCases": [...]}`
    - Implement Step 4: trigger execution via `POST .../testsets/{id}/start` with `{"EnvironmentId": ...}`
    - Implement Step 5: poll `GET .../testsetexecutions/{exec_id}` every 10 seconds until terminal state
    - Apply timeout: if running longer than `UIPATH_EXECUTION_TIMEOUT_SECONDS`, cancel test set and record `outcome=timed_out`
    - Map result: `Passed→passed`, `Failed→failed` (failure_type from payload), `Cancelled→failed/unknown`
    - Extract `screenshot_url` from `ScreenshotUrl` or `screenshot_url` field in result payload; store in `TestResult.screenshot_url`; leave null if absent
    - On any API error during any step: log at ERROR, return `outcome=failed, failure_type=unknown`
    - Tighten fallback logic: if `DEMO_MODE=True` or any required credential is absent → use `MockExecutor` with no warning log
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 12.1, 12.2_

  - [ ]* 2.7 Write unit tests for `UiPathExecutor` (`tests/test_executor_uipath.py`)
    - Mock `requests` to simulate happy-path five-step flow; assert all API calls made in order
    - Test result mapping for `Passed`, `Failed`, `Cancelled`
    - Test timeout path: poll count exceeds threshold → `outcome=timed_out`
    - Test XAML generation failure → `outcome=failed` without upload attempt
    - Test fallback to `MockExecutor` when credentials absent
    - Test `screenshot_url` extracted when present; null when absent
    - _Requirements: 3.1–3.9, 12.1, 12.2_

  - [x] 2.8 Implement `app/services/action_center.py` — ActionCenterService
    - Add config keys to `app/config.py`: `UIPATH_ACTION_CENTER_BASE`, `APPROVAL_SLA_WARN_HOURS: int = 4`, `APPROVAL_SLA_REJECT_HOURS: int = 24`
    - Implement `create_task(pipeline_run, test_cases) -> str` — POST to UiPath Action Center; return task ID; store ID in `PipelineRun.linked_issue_id`
    - Implement `cancel_task(task_id)` — cancel a pending task
    - Implement `poll_task(task_id) -> dict` — return task status and approved/rejected test IDs
    - On `create_task` failure: log at ERROR, return `None` to signal fallback to local approval
    - Only instantiate when `DEMO_MODE=False`
    - _Requirements: 9.1, 9.2, 9.3, 9.7, 9.8_

  - [x] 2.9 Implement `app/services/sla_timer.py` — SLATimerService
    - On `PipelineRun` entering `awaiting_approval` with `DEMO_MODE=False`: schedule two `threading.Timer` callbacks
      - `t = APPROVAL_SLA_WARN_HOURS * 3600`: call `SlackService.send_sla_warning(run_id, task_url)`
      - `t = APPROVAL_SLA_REJECT_HOURS * 3600`: auto-reject all pending `TestCase` records, cancel Action Center task, set `PipelineRun.status = failed` with reason `approval_timeout`
    - On `PipelineRun` leaving `awaiting_approval`: cancel both pending timers
    - In `DEMO_MODE=True`: no-op (existing `approve_all` behaviour is preserved)
    - Wire into `_run_pipeline_async` in `app/main.py` or `app/api/webhooks.py`
    - _Requirements: 9.4, 9.5, 9.6_

  - [ ]* 2.10 Write unit tests for Action Center and SLA timer (`tests/test_action_center.py`)
    - Mock Action Center API; verify `create_task` stores task ID in `linked_issue_id`
    - Test operator full rejection → all test cases set to `rejected`, run set to `failed`
    - Test `create_task` failure → logs ERROR, run stays at `awaiting_approval`
    - Test 4h SLA callback fires Slack warning
    - Test 24h SLA callback auto-rejects all pending tests and cancels AC task
    - _Requirements: 9.1–9.8_

  - [x] 2.11 Update `templates/report.html` — screenshot thumbnails
    - When a test result has a non-null `screenshot_url`, render `<a href="{url}"><img src="{url}" width="120" /></a>`
    - _Requirements: 12.3_

  - [x] 2.12 Phase 2 checkpoint — ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Phase 3 — Database Migrations (Alembic)

  - [x] 3.1 Create `alembic.ini` and `alembic/env.py`
    - `alembic.ini`: standard config with `script_location = alembic`
    - `alembic/env.py`: import `settings.DATABASE_URL`, import `Base` and `app/models` (all models), set `target_metadata = Base.metadata`
    - _Requirements: 5.1_

  - [x] 3.2 Generate baseline migration `alembic/versions/001_initial_schema.py`
    - Run `alembic revision --autogenerate -m "initial_schema"` against a clean database and save the output
    - Manually verify the generated script includes `op.create_table` for all 6 tables: `organisations`, `repositories`, `pipeline_runs`, `test_cases`, `test_results`, `heal_attempts`
    - Verify it includes all 5 composite indexes from `app/models.py`
    - Confirm the script has no `drop_table` operations
    - _Requirements: 5.2, 5.3, 5.4, 5.5_

  - [ ]* 3.3 Write integration test for Alembic migrations
    - Create a pytest fixture that provisions a fresh SQLite (or Postgres) database
    - Run `alembic upgrade head` and assert all 6 tables and 5 indexes exist (via `sqlalchemy inspect`)
    - Run `alembic upgrade head` a second time and assert no error (idempotency)
    - _Requirements: 5.2, 5.3_

  - [ ] 3.4 Phase 3 checkpoint — ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Phase 4 — Frontend & UX

  - [x] 4.1 Add pagination to `GET /api/runs` in `app/api/runs.py`
    - Add `page: int = Query(default=1, ge=1)` and `page_size: int = Query(default=20, ge=1, le=100)` parameters
    - Compute `total`, `offset`, ordered query, and `has_next = (page * page_size) < total`
    - Return response shape: `{"runs": [...], "pagination": {"total", "page", "page_size", "has_next"}}`
    - FastAPI `Query(ge=1, le=100)` handles 422 for out-of-range values automatically
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 4.2 Write unit tests for pagination (`tests/test_pagination.py`)
    - Test page=1 default returns first 20 runs ordered by `created_at` desc
    - Test page=2, page_size=10 returns offset 10–19
    - Test `has_next=true` when more pages remain; `has_next=false` on last page
    - Test empty result on page beyond total: empty `runs` array, `has_next=false`
    - Test `page_size=101` → 422; `page_size=0` → 422
    - _Requirements: 6.1–6.5_

  - [x] 4.3 Add `GET /api/dashboard/runs/{run_id}/details` endpoint in `app/api/dashboard.py`
    - Return: `id`, `pr_number`, `commit_sha`, `status`, `risk_level`, and nested `test_cases` array
    - Each test case includes: `id`, `title`, `test_type`, `priority`, `outcome`, `failure_message`, `screenshot_url`, and `heal_attempts` array (each with `id`, `status`)
    - Return 404 if `run_id` does not exist
    - _Requirements: 8.1, 8.4_

  - [ ] 4.4 Update `templates/dashboard.html` — complete frontend
    - Add login form (hidden once JWT token is stored in `localStorage`); include token in `Authorization: Bearer` header on all API fetches
    - Add "Approve All" button in run rows where `status === "awaiting_approval"`;
      on success replace with "Approved" label; on failure show inline error message
    - Wrap `loadDashboard` fetch in `try/catch`; on failure render a visible error div in the page body
    - `setInterval(loadDashboard, 30000)` for auto-refresh (verify already present or add)
    - Keep vanilla JS only — no build step, no frameworks
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 4.5 Update `templates/report.html` — complete report page
    - Render run header: PR number, first 7 chars of commit SHA, overall risk level, pipeline status
    - Show loading spinner before first fetch resolves; hide on completion
    - Render each test case with: title, `test_type`, priority, outcome, failure message (when failed/timed_out)
    - Render heal attempts per test case (status: proposed/accepted/verified/rejected)
    - When fetch returns 404: display "Run not found" message
    - On network error: display error message and "Retry" button that re-calls `loadReport()`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 4.6 Implement `app/api/orgs.py` — Organisation Management API
    - `GET /api/orgs` → list all `Organisation` records with `id`, `name`, `plan`, `created_at`
    - `POST /api/orgs/{org_id}/repos` → create `Repository` from `{"full_name", "default_branch", "webhook_secret?"}`; return created record
    - `GET /api/orgs/{org_id}/repos` → list repos for the org with `id`, `full_name`, `default_branch`, `is_active`, `created_at`
    - `DELETE /api/orgs/{org_id}/repos/{repo_id}` → soft-delete (`is_active=false`), return 204 No Content
    - On delete: check for active runs (status in `{queued, extracting, generating, awaiting_approval, running}`); if found, raise `HTTPException(409, {"active_run_ids": [...]})`, leave `is_active` unchanged
    - Return 404 if `org_id` or `repo_id` does not exist / does not belong to the org
    - Apply `get_current_user` dependency; apply `require_approver` on write endpoints (POST, DELETE)
    - Mount router in `app/main.py` at prefix `/api/orgs`
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

  - [ ]* 4.7 Write unit tests for Organisation Management API (`tests/test_orgs.py`)
    - Test `GET /api/orgs` returns all orgs
    - Test `POST /api/orgs/{org_id}/repos` creates and returns a new repo
    - Test `GET /api/orgs/{org_id}/repos` returns repos for that org
    - Test `DELETE` soft-delete sets `is_active=false`, returns 204
    - Test `DELETE` with active runs → 409 with `active_run_ids`; `is_active` unchanged
    - Test 404 for unknown `org_id` and unknown `repo_id`
    - _Requirements: 14.1–14.7_

  - [x] 4.8 Phase 4 checkpoint — ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Phase 5 — Operational Hardening

  - [x] 5.1 Add rate limiting via `slowapi` in `app/main.py` and affected routers
    - Add `slowapi` to `requirements.txt`
    - Add `RATE_LIMIT_ENABLED: bool = True` to `app/config.py`
    - In `app/main.py`: instantiate `Limiter(key_func=get_remote_address, enabled=settings.RATE_LIMIT_ENABLED)`, attach to `app.state.limiter`, register `RateLimitExceeded` exception handler
    - Override the handler to also log `WARNING` with source IP and endpoint path
    - Add `@limiter.limit("60/minute")` to `POST /api/webhooks/github`
    - Add `@limiter.limit("120/minute")` to all other `/api/` routes
    - Custom 429 response body: `{"error": "Rate limit exceeded. Retry after {seconds} seconds."}` with `Retry-After` header
    - When `RATE_LIMIT_ENABLED=False`, all checks are skipped automatically via `slowapi`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 5.2 Write unit tests for rate limiting (`tests/test_rate_limit.py`)
    - Send 61 requests to `POST /api/webhooks/github`; assert the 61st returns 429 with `Retry-After` header and `error` JSON body
    - Send 121 requests to another `/api/` endpoint; assert the 121st returns 429
    - Test `RATE_LIMIT_ENABLED=False` skips all limits (no 429 at any count)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 5.3 Add `hypothesis` to `requirements.txt`
    - Add `hypothesis` as a pinned dependency in `requirements.txt`
    - _Requirements: 13.1_

  - [x] 5.4 Create `tests/strategies.py` — Hypothesis strategies
    - `test_result_strategy()` — builds `TestResult` with drawn `TestOutcome` and `TestPriority` values
    - `test_case_strategy()` — builds dicts with 0–20 steps, arbitrary string fields (empty strings, Unicode, XML special chars)
    - `step_strategy()` — builds step dicts with arbitrary `action`/`selector`/`value`/`assertion` strings
    - `valid_json_tests_strategy()` — builds JSON strings in `{"tests": [...]}` shape, optionally wrapped in triple-backtick fences
    - `test_case_schema_strategy()` — builds `TestCaseSchema` objects with drawn enum values
    - _Requirements: 13.2, 13.3, 13.4, 13.5_

  - [ ]* 5.5 Write property test for RiskEngine completeness (Property 7)
    - **Property 7: RiskEngine completeness invariant**
    - **Validates: Requirements 13.2**
    - In `tests/test_pbt.py` using `@given(results=lists(test_result_strategy(), min_size=1))`
    - Assert `report.risk_level in (RiskLevel.low, RiskLevel.medium, RiskLevel.high, RiskLevel.critical)`
    - Use `@settings(max_examples=200)`

  - [ ]* 5.6 Write property test for RiskEngine monotonicity (Property 8)
    - **Property 8: RiskEngine monotonicity invariant**
    - **Validates: Requirements 13.3**
    - In `tests/test_pbt.py` using `@given(base=lists(test_result_strategy()), n_critical=integers(min_value=1, max_value=10))`
    - Append `n_critical` failed P0 results one at a time; assert `risk_level` index is monotonically non-decreasing

  - [ ]* 5.7 Write property test for `_parse_json` invariant (Property 9)
    - **Property 9: `_parse_json` parse invariant**
    - **Validates: Requirements 13.4**
    - In `tests/test_pbt.py` using `@given(payload=valid_json_tests_strategy())`
    - Assert `svc._parse_json(payload)` returns a `dict` without raising any exception

  - [ ]* 5.8 Write property test for `TestCaseSchema` round-trip (Property 10)
    - **Property 10: `TestCaseSchema` serialisation round-trip**
    - **Validates: Requirements 13.5**
    - In `tests/test_pbt.py` using `@given(tc=test_case_schema_strategy())`
    - Assert `TestCaseSchema.model_validate_json(tc.model_dump_json()) == tc`

  - [x] 5.9 Create `Dockerfile`, `docker-compose.yml`, and `.dockerignore`
    - **Dockerfile**: base `python:3.11-slim`, `WORKDIR /app`, copy `requirements.txt`, `RUN pip install --no-cache-dir -r requirements.txt`, copy project, `EXPOSE 8000`
    - **ENTRYPOINT**: `["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]`
    - **docker-compose.yml**: `postgres:15` service with `POSTGRES_DB/USER/PASSWORD`, healthcheck (`pg_isready`), volume `pgdata`; `ghost-qa` service built from `.`, port `8000:8000`, `env_file: .env`, `DATABASE_URL` env override, `depends_on: postgres: condition: service_healthy`
    - **`.dockerignore`**: exclude `.git`, `__pycache__`, `*.pyc`, `*.db`, `venv/`, `.venv/`, `*.egg-info/`, `.pytest_cache/`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_
    - _Note: depends on Phase 3 (Alembic) being complete — the entrypoint runs `alembic upgrade head`_

  - [ ] 5.10 Final checkpoint — ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP delivery
- Each task references specific requirements for traceability
- The only cross-phase hard dependency: **Phase 3 (tasks 3.1–3.2) must complete before task 5.9 (Docker)** because `alembic upgrade head` runs in the container entrypoint
- Phases 1, 2, 4, and 5 (except 5.9) are otherwise independent and can be delivered in parallel
- Property-based tests (tasks 2.2, 2.3, 5.5–5.8) live in `tests/test_pbt.py`; strategies live in `tests/strategies.py`
- Existing test files must continue to pass; override `get_current_user` dependency in existing conftest to return a hardcoded approver payload
- `slowapi` and `hypothesis` must be added to `requirements.txt` as pinned versions

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.5", "3.1", "5.3"] },
    { "id": 1, "tasks": ["1.2", "2.1", "3.2", "4.1", "5.4"] },
    { "id": 2, "tasks": ["1.3", "1.4", "1.5", "2.4", "2.6", "3.3", "4.2"] },
    { "id": 3, "tasks": ["1.6", "1.7", "2.2", "2.3", "2.7", "4.3", "4.6"] },
    { "id": 4, "tasks": ["2.8", "2.9", "4.4", "4.5", "4.7", "5.1"] },
    { "id": 5, "tasks": ["2.10", "2.11", "5.2", "5.5", "5.6", "5.7", "5.8"] },
    { "id": 6, "tasks": ["5.9"] }
  ]
}
```
