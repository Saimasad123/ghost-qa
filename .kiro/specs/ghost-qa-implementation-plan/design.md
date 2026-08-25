# Design Document: Ghost QA — Demo to Production

## Overview

Ghost QA's core demo pipeline is end-to-end functional. This design covers the
fourteen remaining requirements that elevate it to a production-grade system:
JWT authentication with RBAC, real UiPath Test Cloud execution, XAML validity,
Alembic migrations, pagination, frontend completion, UiPath Action Center
approval flow with SLA timers, rate limiting, Docker packaging, screenshot
capture, property-based testing, and an organisation management API.

The guiding constraint throughout is **minimise blast radius**: each new piece
slots into the existing FastAPI / SQLAlchemy / service-layer architecture
without restructuring working code. New modules are addicted alongside existing
ones; existing modules are extended in backward-compatible ways.

---

## Architecture

### Current system (reference)

```
GitHub PR → POST /api/webhooks/github
             └─ _run_pipeline_async (background thread)
                  ├─ GitHubService      (diff / files / issues)
                  ├─ AIBrainService     (test generation, Claude/Grok/demo)
                  ├─ ApprovalService    (approve_all)
                  ├─ ExecutorService    (MockExecutor | UiPathExecutor stub)
                  ├─ HealingService     (LLM heal proposals)
                  ├─ RiskEngine         (risk scoring)
                  ├─ SlackService       (notifications)
                  └─ GitHubService      (PR comment / commit status)

REST API:
  /api/runs, /api/tests, /api/heals, /api/dashboard  ← all open, no auth
  /dashboard, /report/{run_id}                        ← Jinja2 templates
```

### Target system after all 14 requirements

```
                     ┌─────────────────────────────────────────────────────┐
                     │  app/main.py                                         │
                     │  ┌──────────────────────────┐                        │
                     │  │  JWTMiddleware            │  ← Req 1/2            │
                     │  │  RateLimitMiddleware      │  ← Req 10             │
                     │  └──────────────────────────┘                        │
                     │  Routers:                                             │
                     │    /auth          (new)       ← Req 1                │
                     │    /api/runs      (extended)  ← Req 6                │
                     │    /api/orgs      (new)       ← Req 14               │
                     │    /api/webhooks  (unchanged)                        │
                     │    /api/tests     (guard)     ← Req 2                │
                     │    /api/heals     (guard)     ← Req 2                │
                     │    /api/dashboard (unchanged)                        │
                     └─────────────────────────────────────────────────────┘

Services:
  AuthService (new)             ← Req 1/2
  UiPathExecutor (completed)    ← Req 3/12
  XamlGenerator (fixed)         ← Req 4
  ActionCenterService (new)     ← Req 9
  SLATimerService (new)         ← Req 9

Infrastructure:
  alembic/                      ← Req 5
  Dockerfile + docker-compose   ← Req 11
  slowapi rate-limit middleware  ← Req 10

Tests:
  tests/test_pbt.py (Hypothesis) ← Req 13
```

All five phases are independent of each other and can be delivered in
parallel. The only hard dependency is Phase 3 (Alembic) which must be
complete before Phase 5 (Docker) because the Dockerfile entrypoint runs
`alembic upgrade head`.

---

## Components and Interfaces

### Phase 1 – Security & Auth

#### AuthService (`app/services/auth.py`)

Replaces the stub in `src/auth.py` with a real implementation wired into
FastAPI's dependency injection.

```python
class AuthService:
    def create_token(self, email: str, role: str) -> dict:
        """Return {"token": str, "expires_in": int}."""

    def verify_token(self, token: str) -> dict:
        """Return decoded payload or raise HTTPException(401)."""

    def require_role(self, required: str) -> Callable:
        """Return a FastAPI dependency that raises 403 if role insufficient."""
```

Token structure (JWT payload):

```json
{
  "sub": "user@example.com",
  "role": "viewer" | "approver",
  "exp": <unix timestamp>,
  "iat": <unix timestamp>
}
```

Signing: HS256, key = `settings.SECRET_KEY`.

Expiry: `settings.JWT_EXPIRY_MINUTES` (new config field, validated 15–1440).

#### Auth router (`app/api/auth.py`)

```
POST /auth/login
  Body: {"email": str, "password": str}
  200:  {"token": str, "expires_in": int}
  401:  {"detail": "Invalid credentials"}
```

Credential store for Phase 1: a small in-memory dict of
`email → (hashed_password, role)` seeded from environment variables
`AUTH_USERS` (JSON string). This avoids introducing a user table while
satisfying the acceptance criteria.

#### JWT middleware (`app/middleware/auth.py`)

A `BaseHTTPMiddleware` subclass that:

1. Skips paths that do not start with `/api/` and `POST /auth/login`.
2. Reads the `Authorization: Bearer <token>` header.
3. Calls `auth_service.verify_token(token)`.
4. On success, attaches the decoded payload to `request.state.user`.
5. On failure, returns `JSONResponse({"detail": "..."}, status_code=401)`.

#### RBAC dependency (`app/dependencies.py`)

```python
def require_approver(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "approver":
        raise HTTPException(403, "Approver role required")
    return user
```

Applied to: `POST /api/tests/{id}/approve`, `POST /api/tests/{id}/reject`,
`POST /api/runs/{id}/approve`, `POST /api/heals/{id}/approve`,
`POST /api/heals/{id}/reject`, `POST /api/heals/{id}/execute`.

---

### Phase 2 – Real UiPath Execution

#### UiPathExecutor — complete implementation (`app/services/executor.py`)

The existing `UiPathExecutor.execute_test` method has a `# work in progress`
stub. The replacement implements the full five-step flow:

**Step 1 – Authenticate** (already working via `_get_access_token`).

**Step 2 – Upload XAML to Test Manager**

```
POST https://cloud.uipath.com/{org_id}/{tenant}/testmanager_/api/v1/testcases
Content-Type: multipart/form-data
Authorization: Bearer {token}
Body: file=<xaml bytes>, name=<test_case.title>
Response: {"Id": "<uipath_test_id>"}
```

Store `uipath_test_id` on the `TestCase` record.

**Step 3 – Create test set**

```
POST https://cloud.uipath.com/{org_id}/{tenant}/testmanager_/api/v1/testsets
Body: {"Name": "GhostQA-{run_id[:8]}", "TestCases": [{"TestCaseId": uipath_test_id}]}
Response: {"Id": "<test_set_id>"}
```

**Step 4 – Trigger execution**

```
POST https://cloud.uipath.com/{org_id}/{tenant}/testmanager_/api/v1/testsets/{test_set_id}/start
Body: {"EnvironmentId": settings.UIPATH_ENVIRONMENT_ID}
Response: {"TestSetExecutionId": "<exec_id>"}
```

**Step 5 – Poll for completion**

```
GET https://cloud.uipath.com/{org_id}/{tenant}/testmanager_/api/v1/testsetexecutions/{exec_id}
Poll interval: 10 seconds
Timeout: settings.UIPATH_EXECUTION_TIMEOUT_SECONDS (default 300)
Terminal states: Passed, Failed, Cancelled, TimedOut
```

Result mapping:

| UiPath status | `TestOutcome` | `FailureType`  |
|---------------|---------------|----------------|
| Passed        | passed        | —              |
| Failed        | failed        | from payload   |
| Cancelled     | failed        | unknown        |
| timeout       | timed_out     | —              |

**New config keys** (added to `app/config.py`):

```python
UIPATH_EXECUTION_TIMEOUT_SECONDS: int = 300
UIPATH_TEST_MANAGER_BASE: str = "https://cloud.uipath.com"
```

**Fallback logic** (Req 3.8): `ExecutorService.__init__` already checks whether
all five credentials are present. The condition is tightened to not emit the
"work in progress" warning — instead it silently routes to `MockExecutor`.

#### Screenshot capture (`TestResult.screenshot_url`)

After polling reaches a terminal state, the executor inspects the execution
result payload for a `ScreenshotUrl` field:

```python
screenshot_url = result_payload.get("ScreenshotUrl") or result_payload.get("screenshot_url")
```

Stored in `TestResult.screenshot_url` (column already exists in `models.py`).
If absent, the field remains `null` — no error is raised (Req 12.2).

#### XamlGenerator — fixed well-formed XML (`app/services/xaml_generator.py`)

The existing generator produces invalid XML (unclosed tags, invalid attribute
syntax). The rewrite uses Python's `xml.etree.ElementTree` to build the tree
programmatically so it is always well-formed.

Key structural decisions:

- Root element: `<Activity>` with the correct UiPath namespace declarations.
- `xmlns:ui="http://schemas.uipath.com/workflow/activities"` on the root.
- Each step produces exactly one `<ui:Sequence>` child of `<ui:FlowStep>`.
- Empty-step test cases produce a `<ui:FlowStep>` with no children.
- `ET.tostring(root, encoding="unicode", xml_declaration=True)` guarantees
  well-formed serialisation.

XAML structure:

```xml
<?xml version='1.0' encoding='utf-8'?>
<Activity
  xmlns="http://schemas.microsoft.com/netfx/2013/xaml/activities"
  xmlns:ui="http://schemas.uipath.com/workflow/activities"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  DisplayName="Test_{id}">
  <ui:FlowStep DisplayName="Execute Test">
    <!-- N × <ui:Sequence> elements, one per step -->
    <ui:Sequence DisplayName="Step 1: {action}">
      <!-- optional children: ui:UiBrowser, ui:TypeInto, ui:VerifyExpression -->
    </ui:Sequence>
  </ui:FlowStep>
</Activity>
```

---

### Phase 3 – Database Migrations

#### Alembic configuration

New files:

```
alembic.ini                    ← standard Alembic config, script_location=alembic
alembic/
  env.py                       ← reads settings.DATABASE_URL, imports Base
  versions/
    001_initial_schema.py      ← baseline: all 6 tables + 5 indexes
```

`env.py` pattern:

```python
from app.config import settings
from app.database import Base
from app.models import *  # noqa — ensure all models are registered

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata
```

Migration `001_initial_schema.py` is generated via `alembic revision
--autogenerate -m "initial_schema"` against an empty database, then reviewed
to confirm all six tables and five composite indexes are present. It will
contain `op.create_table` and `op.create_index` calls. Running it a second
time is idempotent because Alembic tracks the revision in the `alembic_version`
table.

**Note on `PipelineRun.linked_issue_id`**: This existing column is repurposed
by Req 9 to also store the UiPath Action Center task ID. No schema change is
required — the field is already `String, nullable=True`.

---

### Phase 4 – Frontend & UX

#### Pagination (`app/api/runs.py`)

The `GET /api/runs` handler gains two query parameters:

```python
@router.get("/")
def get_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),  # auth
):
```

Response shape:

```json
{
  "runs": [...],
  "pagination": {
    "total": 142,
    "page": 2,
    "page_size": 10,
    "has_next": true
  }
}
```

Implementation:

```python
total = db.query(func.count(PipelineRun.id)).scalar()
offset = (page - 1) * page_size
runs = db.query(PipelineRun).order_by(PipelineRun.created_at.desc())\
         .offset(offset).limit(page_size).all()
has_next = (page * page_size) < total
```

FastAPI's `Query(ge=1, le=100)` automatically returns 422 for out-of-range
values, satisfying Req 6.4.

#### Dashboard frontend (`templates/dashboard.html`)

Current status: JS fetches `/api/dashboard/overview` and populates stats and
table. Missing:

1. **Approve-All button in run row** — add a button when `run.status === "awaiting_approval"`.
   On click: `POST /api/runs/{run_id}/approve` with `Authorization: Bearer <token>`.
   On success: replace button with "Approved" label. On failure: render inline error.
2. **Error state** — wrap the `loadDashboard` fetch in `try/catch`; on failure,
   insert a visible error div (not just `console.error`).
3. **Auto-refresh** — `setInterval(loadDashboard, 30000)` is already present.
4. **Token storage** — if JWT auth is required, the dashboard must read a token
   from `localStorage` and include it in fetch headers. A minimal login form is
   added at the top of `dashboard.html` that is hidden once a token is stored.

The dashboard remains vanilla JS + inline CSS — no build step.

#### Report page (`templates/report.html`)

Current status: JS fetches `/api/runs/{run_id}/report` (summary) and
`/api/runs/{run_id}/tests` (test list). Missing:

1. **Run header** — PR number, first 7 chars of commit SHA, overall risk, pipeline
   status. These are already in the report response; the template just needs to
   render them.
2. **Loading indicator** — a spinner div shown before the first fetch resolves,
   hidden on completion.
3. **Heal attempts per test** — fetched from the new
   `GET /api/dashboard/runs/{run_id}/details` endpoint (see below).
4. **Not-found state** — when the fetch returns 404, render "Run not found".
5. **Network error + retry** — when fetch fails with a network error, show an
   error message and a "Retry" button that re-calls `loadReport()`.
6. **Screenshot thumbnails** — if a test result has `screenshot_url`, render
   `<a href="{url}"><img src="{url}" width="120" /></a>`.

#### Dashboard details endpoint (`app/api/dashboard.py`)

A new endpoint to satisfy the report page data needs:

```
GET /api/dashboard/runs/{run_id}/details
```

Response:

```json
{
  "id": "...",
  "pr_number": 42,
  "commit_sha": "abc1234",
  "status": "completed",
  "risk_level": "medium",
  "test_cases": [
    {
      "id": "...",
      "title": "...",
      "test_type": "functional",
      "priority": "p1_high",
      "outcome": "failed",
      "failure_message": "...",
      "screenshot_url": null,
      "heal_attempts": [
        {"id": "...", "status": "proposed"}
      ]
    }
  ]
}
```

#### Organisation Management API (`app/api/orgs.py`)

New router, mounted at `/api/orgs`.

```
GET    /api/orgs                          → list orgs
POST   /api/orgs/{org_id}/repos           → create repo
GET    /api/orgs/{org_id}/repos           → list repos
DELETE /api/orgs/{org_id}/repos/{repo_id} → soft-delete (is_active=false)
```

Request body for POST:

```json
{"full_name": "owner/repo", "default_branch": "main", "webhook_secret": "optional"}
```

DELETE conflict check (Req 14.5):

```python
active_statuses = {PipelineStatus.queued, PipelineStatus.extracting,
                   PipelineStatus.generating, PipelineStatus.awaiting_approval,
                   PipelineStatus.running}
active_runs = db.query(PipelineRun).filter(
    PipelineRun.repository_id == repo_id,
    PipelineRun.status.in_(active_statuses)
).all()
if active_runs:
    raise HTTPException(409, {"active_run_ids": [r.id for r in active_runs]})
```

All org/repo endpoints require `get_current_user` dependency (viewer read,
approver write).

---

### Phase 5 – Operational Hardening

#### Rate limiting (`app/middleware/rate_limit.py`)

Library: **`slowapi`** (FastAPI-native limiter built on `limits`).

Configuration in `app/config.py`:

```python
RATE_LIMIT_ENABLED: bool = True
```

Limiter setup in `app/main.py`:

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address, enabled=settings.RATE_LIMIT_ENABLED)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

Per-endpoint decorators:

```python
# webhooks.py
@router.post("/github")
@limiter.limit("60/minute")
async def github_webhook(request: Request): ...

# all other /api/ routes — applied via a shared decorator factory
@router.get("/")
@limiter.limit("120/minute")
def get_runs(request: Request, ...): ...
```

The `_rate_limit_exceeded_handler` in `slowapi` returns 429 with a
`Retry-After` header automatically. A custom override adds:

```json
{"error": "Rate limit exceeded. Retry after {seconds} seconds."}
```

Logging on rate-limit violation:

```python
# override handler to also log
logger.warning(f"Rate limit exceeded: ip={request.client.host} path={request.url.path}")
```

When `RATE_LIMIT_ENABLED=False`, `slowapi`'s `enabled=False` flag skips all
checks. No endpoint code changes are needed.

#### Docker (`Dockerfile`, `docker-compose.yml`, `.dockerignore`)

**Dockerfile**:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
ENTRYPOINT ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

**docker-compose.yml**:

```yaml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: ghost_qa
      POSTGRES_USER: ghost_qa
      POSTGRES_PASSWORD: password
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ghost_qa"]
      interval: 5s
      timeout: 5s
      retries: 10

  ghost-qa:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      DATABASE_URL: postgresql://ghost_qa:password@postgres:5432/ghost_qa
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  pgdata:
```

**`.dockerignore`**:

```
.git
__pycache__
*.pyc
*.db
venv/
.venv/
*.egg-info/
.pytest_cache/
```

#### Action Center Service (`app/services/action_center.py`)

New service, only active when `DEMO_MODE=False`.

New config keys:

```python
UIPATH_ACTION_CENTER_BASE: str = "https://cloud.uipath.com"
APPROVAL_SLA_WARN_HOURS: int = 4
APPROVAL_SLA_REJECT_HOURS: int = 24
```

Interface:

```python
class ActionCenterService:
    def create_task(self, pipeline_run: PipelineRun, test_cases: List[TestCase]) -> str:
        """Create an Action Center task; return task_id."""

    def cancel_task(self, task_id: str) -> None:
        """Cancel a pending task."""

    def poll_task(self, task_id: str) -> dict:
        """Return task status and approved/rejected test IDs."""
```

The task creation payload follows the UiPath Action Center "Action" API:

```json
{
  "Title": "Ghost QA: Approve tests for PR #{pr_number}",
  "Priority": "Medium",
  "Data": {
    "pipelineRunId": "...",
    "tests": [{"id": "...", "title": "...", "risk": "high"}]
  },
  "ActionCatalogName": "GhostQA_Approval"
}
```

The task ID is stored in `PipelineRun.linked_issue_id` (existing column,
repurposed).

#### SLA Timer Service (`app/services/sla_timer.py`)

A lightweight background scheduler (using `threading.Timer` or APScheduler)
that:

1. On `PipelineRun` entering `awaiting_approval`, schedules two callbacks:
   - `t=4h`: Slack warning via `SlackService.send_sla_warning`.
   - `t=24h`: Auto-rejection via `ActionCenterService.cancel_task` +
     set all pending `TestCase.approval_status = rejected` +
     set `PipelineRun.status = failed`.

2. On `PipelineRun` leaving `awaiting_approval` (approved or manually
   rejected), cancels both pending timers.

In `DEMO_MODE=True`, the timer service does nothing and `approve_all` is
called immediately (existing behaviour preserved).

---

## Data Models

No new ORM models are required. The following columns are added via Alembic
migration `002_add_auth_fields.py` if needed:

| Table | Column | Type | Purpose |
|-------|--------|------|---------|
| `pipeline_runs` | `linked_issue_id` (exists) | String | Action Center task ID |
| `test_cases` | `screenshot_url` (exists) | String | UiPath screenshot URL |

Two new config fields added to `app/config.py`:

```python
JWT_EXPIRY_MINUTES: int = Field(default=60, ge=15, le=1440)
RATE_LIMIT_ENABLED: bool = True
UIPATH_EXECUTION_TIMEOUT_SECONDS: int = 300
AUTH_USERS: str = '{"admin@ghost.qa": {"password_hash": "...", "role": "approver"}}'
APPROVAL_SLA_WARN_HOURS: int = 4
APPROVAL_SLA_REJECT_HOURS: int = 24
```

`JWT_EXPIRY_MINUTES` validation: pydantic `Field(ge=15, le=1440)` raises a
`ValidationError` at import time if the value is out of range, preventing
application startup (Req 1.8).

---

## Key Algorithms and Flows

### JWT Authentication Flow

```
Client                       FastAPI middleware                 AuthService
  │                                │                                │
  ├─ POST /auth/login ──────────────▶ (bypassed by middleware)       │
  │   {"email","password"}          │                                │
  │                                 ├───────────────────────────────▶│
  │                                 │                    login()     │
  │                                 │◀───────────────────────────────│
  │◀── {"token", "expires_in"} ─────│                                │
  │                                 │                                │
  ├─ GET /api/runs ─────────────────▶                                │
  │   Authorization: Bearer <jwt>   │                                │
  │                                 ├─ verify_token() ──────────────▶│
  │                                 │  success: attach to request    │
  │                                 ├─ forward to handler            │
  │◀── 200 {"runs": [...]} ─────────│                                │
```

### UiPath Execution Flow (non-demo)

```
ExecutorService.execute_tests(approved_tests)
  └─ UiPathExecutor.execute_batch(tests)
       └─ for each test:
            1. _get_access_token()           ← cached, refreshed on expiry
            2. XamlGenerator.generate_xaml() ← well-formed XML
            3. POST /testmanager/.../testcases (upload XAML)
            4. POST /testmanager/.../testsets  (create set)
            5. POST /testmanager/.../start     (trigger)
            6. poll GET /testsetexecutions/... every 10s
               until terminal state or UIPATH_EXECUTION_TIMEOUT_SECONDS
            7. map result → TestOutcome
            8. extract screenshot_url if present
            9. return TestResult
```

### Action Center SLA Flow

```
_run_pipeline() → PipelineStatus.awaiting_approval
                   │
          DEMO_MODE=True          DEMO_MODE=False
                   │                     │
            approve_all()         ActionCenterService.create_task()
                   │              store task_id → linked_issue_id
                   │              SLATimerService.schedule(run_id)
                   │                     │
                   │              +4h: SlackService.send_sla_warning()
                   │              +24h: auto-reject all pending tests
                   │                    cancel_task()
                   │                    run.status = failed
                   │
                   ▼
            PipelineStatus.running → _execute_tests()
```

### Pagination Algorithm

```python
total = count(pipeline_runs)
offset = (page - 1) * page_size
rows = SELECT ... ORDER BY created_at DESC LIMIT page_size OFFSET offset
has_next = (page * page_size) < total
```

Edge cases:
- `page=1, page_size=20, total=0` → empty rows, `has_next=false`.
- `page=3, page_size=10, total=25` → offset=20, rows=[row21..row25] (5 rows), `has_next=false`.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Invalid JWT tokens are always rejected

*For any* string that is not a valid JWT signed with `SECRET_KEY` (including
random strings, tokens signed with a different key, and well-formed JWTs with
an expired `exp` claim), the JWT middleware SHALL return HTTP 401.

**Validates: Requirements 1.4, 1.5**

---

### Property 2: Viewer role is blocked on every write endpoint

*For any* valid, unexpired JWT with `role="viewer"`, every write endpoint
(`POST /api/tests/{id}/approve`, `POST /api/tests/{id}/reject`,
`POST /api/runs/{id}/approve`, `POST /api/heals/{id}/approve`,
`POST /api/heals/{id}/reject`, `POST /api/heals/{id}/execute`) SHALL return
HTTP 403.

**Validates: Requirements 2.2, 2.5**

---

### Property 3: Unknown or missing role claim is always denied

*For any* valid, unexpired JWT whose `role` claim is absent or not exactly
`"viewer"` or `"approver"`, every endpoint SHALL return HTTP 403.

**Validates: Requirements 2.4**

---

### Property 4: XAML is always well-formed XML

*For any* test case dict with any non-negative number of steps (each step having
arbitrary action, selector, value, and assertion strings), `XamlGenerator.generate_xaml()`
SHALL return a string that `xml.etree.ElementTree.fromstring()` can parse
without raising `ParseError`.

**Validates: Requirements 4.1, 4.2**

---

### Property 5: XAML step count round-trip invariant

*For any* test case dict with N steps (N ≥ 0), `XamlGenerator.generate_xaml()`
SHALL produce XAML containing exactly N `<ui:Sequence>` elements as direct
children of `<ui:FlowStep>`.

**Validates: Requirements 4.3, 4.4, 4.5**

---

### Property 6: Pagination slice correctness

*For any* database state with T total pipeline runs and any valid combination of
`page` (≥ 1) and `page_size` (1–100), the `GET /api/runs` response SHALL satisfy:

- `pagination.total` == T
- `len(runs)` == min(page_size, max(0, T − (page−1)×page_size))
- `pagination.has_next` == (page × page_size < T)

**Validates: Requirements 6.2, 6.3, 6.5**

---

### Property 7: RiskEngine completeness invariant

*For any* non-empty list of `TestResult` objects with any combination of
`TestPriority` and `TestOutcome` values, `RiskEngine.calculate_risk()` SHALL
return a `risk_level` that is exactly one of `RiskLevel.low`, `RiskLevel.medium`,
`RiskLevel.high`, or `RiskLevel.critical` — it never raises an exception and
never returns `None` or an unrecognised value.

**Validates: Requirements 13.2**

---

### Property 8: RiskEngine monotonicity invariant

*For any* base set of `TestResult` objects, as additional results with
`outcome=failed` and `priority=p0_critical` are appended one at a time (all
other inputs held constant), the sequence of `risk_level` values returned by
`RiskEngine.calculate_risk()` is monotonically non-decreasing in the partial
order `low ≤ medium ≤ high ≤ critical`.

**Validates: Requirements 13.3**

---

### Property 9: `_parse_json` parse invariant

*For any* JSON string that conforms to the `{"tests": [...]}` schema accepted
by `AIBrainService._parse_json()` (including strings wrapped in triple-backtick
code fences), the method SHALL return a `dict` without raising any exception.

**Validates: Requirements 13.4**

---

### Property 10: `TestCaseSchema` serialisation round-trip

*For any* valid `TestCaseSchema` object (with any combination of `TestType`,
`TestPriority`, `RiskLevel`, step count, and string content), calling
`.model_dump_json()` followed by `TestCaseSchema.model_validate_json()` SHALL
produce an object equal to the original.

**Validates: Requirements 13.5**

---

### Property 11: Repository deactivation conflict guard

*For any* repository that has at least one associated `PipelineRun` with
`status` in `{queued, extracting, generating, awaiting_approval, running}`,
`DELETE /api/orgs/{org_id}/repos/{repo_id}` SHALL return HTTP 409 and leave
`Repository.is_active` unchanged.

**Validates: Requirements 14.5**

---

## Error Handling

| Layer | Failure mode | Handling |
|-------|-------------|---------|
| JWT middleware | Missing/invalid/expired token | Return 401 JSON response; do not call handler |
| RBAC dependency | Insufficient role | Raise HTTPException(403); FastAPI converts to JSON |
| UiPath API (any step) | HTTP error or exception | Log at ERROR; return TestResult(outcome=failed, failure_type=unknown) |
| XAML generation failure | Any exception in generate_xaml() | Log at ERROR; return TestResult(outcome=failed, failure_type=unknown) without upload |
| Action Center creation | API failure | Log at ERROR; leave run at awaiting_approval; fall back to local approval API |
| SLA timer (24h) | Slack unreachable | Log at WARNING; continue with auto-reject |
| Rate limiter | Limit exceeded | Return 429 + Retry-After; log WARNING with IP and path |
| Alembic migrations | Target DB unreachable at startup | Process exits non-zero; Docker restarts container |
| Pagination params | page_size < 1 or > 100 | FastAPI Query validation returns 422 automatically |
| Org/repo not found | DB query returns None | Raise HTTPException(404) |
| Repo delete with active runs | Active runs exist | Raise HTTPException(409) with run ID list |

All unhandled exceptions in the pipeline background thread are caught at the
`_run_pipeline_async` level (existing behaviour) and set the run status to
`failed`. This remains unchanged.

---

## Testing Strategy

### Unit tests (example-based)

New test files alongside existing `tests/`:

- `tests/test_auth.py` — login success/failure, token creation, middleware
  bypass on public routes, 401 on missing token, 403 on wrong role.
- `tests/test_xaml_generator.py` — well-formed output for 0/1/N steps,
  namespace present, Sequence count matches step count (a few fixed examples
  alongside the property tests).
- `tests/test_executor_uipath.py` — mock `requests` to verify the five-step
  UiPath flow, result mapping, timeout path, fallback to mock when credentials
  absent.
- `tests/test_pagination.py` — specific page/page_size combinations, 422 for
  out-of-range params.
- `tests/test_orgs.py` — CRUD happy paths, 404 for unknown org/repo, 409
  conflict on delete-with-active-runs.
- `tests/test_rate_limit.py` — send limit+1 requests, assert 429 + Retry-After,
  confirm RATE_LIMIT_ENABLED=False skips.
- `tests/test_action_center.py` — mock Action Center API calls, verify task
  creation stores task ID, SLA callbacks trigger correct state transitions.

### Property-based tests (`tests/test_pbt.py`)

Uses `hypothesis`. Each test is tagged with the property it validates.

```python
# Feature: ghost-qa-implementation-plan, Property 4: XAML well-formed
# Validates: Requirements 4.1, 4.2
@given(test_case=test_case_strategy())
@settings(max_examples=200)
def test_xaml_always_well_formed(test_case):
    xaml = XamlGenerator().generate_xaml(test_case)
    ET.fromstring(xaml)  # ParseError would fail the test

# Feature: ghost-qa-implementation-plan, Property 5: XAML step count round-trip
# Validates: Requirements 4.3, 4.4, 4.5
@given(steps=lists(step_strategy(), min_size=0, max_size=50))
@settings(max_examples=200)
def test_xaml_step_count_roundtrip(steps):
    tc = {"id": "x", "title": "t", "steps": steps}
    xaml = XamlGenerator().generate_xaml(tc)
    root = ET.fromstring(xaml)
    ns = {"ui": "http://schemas.uipath.com/workflow/activities"}
    flow_step = root.find(".//ui:FlowStep", ns)
    seqs = flow_step.findall("ui:Sequence", ns) if flow_step is not None else []
    assert len(seqs) == len(steps)

# Feature: ghost-qa-implementation-plan, Property 7: RiskEngine completeness
# Validates: Requirement 13.2
@given(results=lists(test_result_strategy(), min_size=1))
@settings(max_examples=200)
def test_risk_engine_completeness(results):
    engine = RiskEngine()
    report = engine.calculate_risk("run-1", results, [])
    assert report.risk_level in (RiskLevel.low, RiskLevel.medium,
                                  RiskLevel.high, RiskLevel.critical)

# Feature: ghost-qa-implementation-plan, Property 8: RiskEngine monotonicity
# Validates: Requirement 13.3
@given(base=lists(test_result_strategy()), n_critical=integers(min_value=1, max_value=10))
@settings(max_examples=200)
def test_risk_engine_monotonicity(base, n_critical):
    engine = RiskEngine()
    risk_order = [RiskLevel.low, RiskLevel.medium, RiskLevel.high, RiskLevel.critical]
    prev_idx = 0
    results = list(base)
    for _ in range(n_critical):
        results.append(make_failed_p0_result())
        report = engine.calculate_risk("run-1", results, [])
        idx = risk_order.index(report.risk_level)
        assert idx >= prev_idx
        prev_idx = idx

# Feature: ghost-qa-implementation-plan, Property 9: _parse_json invariant
# Validates: Requirement 13.4
@given(payload=valid_json_tests_strategy())
@settings(max_examples=200)
def test_parse_json_no_exception(payload):
    svc = AIBrainService()
    result = svc._parse_json(payload)
    assert isinstance(result, dict)

# Feature: ghost-qa-implementation-plan, Property 10: TestCaseSchema round-trip
# Validates: Requirement 13.5
@given(tc=test_case_schema_strategy())
@settings(max_examples=200)
def test_test_case_schema_roundtrip(tc):
    serialised = tc.model_dump_json()
    restored = TestCaseSchema.model_validate_json(serialised)
    assert restored == tc
```

Hypothesis strategies defined in `tests/strategies.py`:

- `test_result_strategy()` — builds `TestResult` with drawn `TestOutcome` and
  `TestPriority` values.
- `test_case_strategy()` — builds dicts with random steps (0–20 steps, each
  with arbitrary string fields, including empty strings and Unicode).
- `step_strategy()` — builds step dicts with arbitrary action/selector/value/
  assertion strings (including empty, whitespace-only, and XML special chars).
- `valid_json_tests_strategy()` — builds JSON strings in the `{"tests": [...]}`
  shape, optionally wrapped in triple-backtick fences.
- `test_case_schema_strategy()` — builds `TestCaseSchema` objects with drawn
  enum values.

All property tests are configured with `max_examples=200` to satisfy Req 13's
"minimum 100 iterations" requirement.

### Integration tests

- Alembic: a pytest fixture creates a fresh SQLite DB, runs `alembic upgrade
  head`, and inspects `sqlite_master` for all six tables and five indexes.
- Docker: verified manually via `docker compose up`; not automated in CI (no
  Docker-in-Docker).
- UiPath end-to-end: skipped in CI unless `UIPATH_INTEGRATION_TEST=1` is set.

### Existing tests

The 8 existing test files (`test_ai_brain.py`, `test_approval.py`,
`test_database.py`, `test_executor.py`, `test_healing.py`, `test_risk.py`,
`test_webhook.py`, and `conftest.py`) continue to pass without modification.
JWT middleware is bypassed in tests by injecting a `override_dependency` for
`get_current_user` that returns a hardcoded approver payload.

---

## External Integrations Guide

This section covers every external system Ghost QA connects to, how to obtain
credentials for each one, how the connection works end-to-end, and exactly
which environment variables to set in your `.env` file.

---

### 1. UiPath Cloud — Test Cloud & Orchestrator

#### What it does

UiPath is the engine that actually runs your generated tests on a real robot.
Ghost QA uploads each test as a `.xaml` workflow file, creates a test set,
triggers execution on a robot in your configured environment, and polls until
the run completes. Results (Pass / Fail / screenshot URL) are pulled back and
stored in Ghost QA's database.

#### How to get credentials

1. **Create a UiPath Cloud account** at [cloud.uipath.com](https://cloud.uipath.com) — the Community edition is free and includes Test Cloud.
2. **Create an external application** (OAuth client):
   - Go to **Admin → External Applications → Add Application**
   - Application type: **Confidential**
   - Scopes to enable: `OR.TestSets`, `OR.TestSetExecutions`, `OR.TestCases`, `OR.Assets`, `OR.Robots.Read`
   - Click **Add** and copy the **Client ID** and **Client Secret** — you only see the secret once.
3. **Find your Organisation ID**:
   - In the URL after you log in: `cloud.uipath.com/{YOUR_ORG_ID}/{tenant}/...`
   - Or: Admin → Organisations → copy the Organisation Name (used as `UIPATH_ORG_ID`)
4. **Find your Tenant Name**:
   - Visible in the URL and in Admin → Tenants
5. **Find your Environment ID**:
   - Go to Orchestrator → **Test** → **Environments**
   - Create a new environment called `GhostQA` and assign at least one robot to it
   - The environment ID is shown in the URL when you open the environment
6. **Set up Test Manager**:
   - Go to **Test Manager** (top navigation)
   - Create a project called `GhostQA` — Ghost QA will upload test cases here

#### Environment variables to set

```ini
UIPATH_CLIENT_ID=your_client_id_here
UIPATH_CLIENT_SECRET=your_client_secret_here
UIPATH_TENANT_NAME=your_tenant_name_here
UIPATH_ORG_ID=your_org_name_or_id_here
UIPATH_ENVIRONMENT_ID=your_environment_uuid_here
UIPATH_TEST_FOLDER=GhostQA
UIPATH_EXECUTION_TIMEOUT_SECONDS=300
DEMO_MODE=false
```

#### How the connection works — complete flow

```
Ghost QA (Python)                    UiPath Cloud
     │                                     │
     │ 1. POST /identity/connect/token     │
     │    grant_type=client_credentials    │
     │    client_id, client_secret         │
     │    scope=OR.AuthAPI                 │
     │ ──────────────────────────────────► │
     │ ◄─────────────── {access_token} ── │
     │                                     │
     │ 2. POST /testmanager_/api/v1/       │
     │       testcases                     │
     │    multipart: file=<xaml>, name=... │
     │ ──────────────────────────────────► │
     │ ◄──────────── {"Id": test_case_id} │
     │                                     │
     │ 3. POST /testmanager_/api/v1/       │
     │       testsets                      │
     │    {"Name":"GhostQA-xxx",           │
     │     "TestCases":[{test_case_id}]}   │
     │ ──────────────────────────────────► │
     │ ◄──────────────── {"Id": set_id} ─ │
     │                                     │
     │ 4. POST /testmanager_/api/v1/       │
     │       testsets/{set_id}/start       │
     │    {"EnvironmentId": env_id}        │
     │ ──────────────────────────────────► │
     │ ◄──── {"TestSetExecutionId": exec}  │
     │                                     │
     │ 5. GET /testmanager_/api/v1/        │  (every 10 seconds)
     │       testsetexecutions/{exec}      │
     │ ──────────────────────────────────► │
     │ ◄── {"Status":"Running/Passed/..."}─│
     │    (loop until terminal or timeout) │
     │                                     │
     │ 6. Map result → TestOutcome         │
     │    Extract ScreenshotUrl if present │
     │    Store in database                │
```

The token is cached in memory and refreshed automatically 60 seconds before
expiry (handled by `UiPathExecutor._get_access_token()`).

#### UiPath Action Center (human approval gate)

When `DEMO_MODE=false`, instead of auto-approving AI-generated tests, Ghost QA
creates a task in **UiPath Action Center** for a human to review:

1. A reviewer opens Action Center in the UiPath web portal
2. They see the list of generated tests with their risk levels
3. They approve or reject the batch
4. Ghost QA polls Action Center and picks up the decision
5. Approved tests proceed to execution; rejected tests are skipped

To set this up:
- In UiPath Studio, create an **Action Catalog** item named `GhostQA_Approval`
- Deploy it to Orchestrator in the same tenant
- The catalog name must match `ActionCatalogName` in the task creation payload

**SLA timers**: If the approval task sits untouched for 4 hours, Ghost QA sends
a Slack warning. After 24 hours it auto-rejects all pending tests and marks the
pipeline as failed.

---

### 2. XAI / Grok (AI test generation)

#### What it does

XAI's Grok model is the primary AI brain for generating test cases from PR
diffs. It reads the code change, understands the intent, and produces 5
structured test cases with steps, selectors, priority, and risk rationale.
Ghost QA also uses it to detect test debt and propose self-healing fixes for
failing tests.

#### How to get credentials

1. Go to [console.x.ai](https://console.x.ai) and sign in with your X (Twitter) account
2. Navigate to **API Keys** → **Create API Key**
3. Copy the key — it begins with `xai-`
4. Note the model name you want to use (e.g. `grok-4-1-fast-reasoning` for fast responses, or `grok-2` for more thorough analysis)

#### Environment variables to set

```ini
XAI_API_KEY=xai-your_api_key_here
GROK_MODEL=grok-4-1-fast-reasoning
```

#### How the connection works

Ghost QA uses the **OpenAI-compatible API** that XAI exposes, so no special SDK
is needed — it reuses the `openai` Python package already in `requirements.txt`:

```python
from openai import OpenAI

client = OpenAI(
    api_key="xai-your_key",
    base_url="https://api.x.ai/v1"   # ← XAI endpoint, not OpenAI
)

response = client.chat.completions.create(
    model="grok-4-1-fast-reasoning",
    messages=[
        {"role": "system", "content": "You are a senior QA engineer..."},
        {"role": "user", "content": "Analyze this PR diff and generate tests..."}
    ],
    max_tokens=4096,
    timeout=30
)
test_json = response.choices[0].message.content
```

The `AIBrainService` in `app/services/ai_brain.py` already implements this.
It validates the key format (must start with `xai-` or `sk-`), initialises the
client, and falls back to demo mode if the key is missing or invalid.

#### XAI vs Anthropic Claude — which one wins?

Ghost QA tries XAI first. If `XAI_API_KEY` is set and valid, Grok is used for
all AI calls. If not, it tries `ANTHROPIC_API_KEY`. If neither is set, it falls
back to the built-in demo responses (deterministic, no external call).

You can set both keys — XAI takes priority because it is checked first in
`AIBrainService.__init__`.

---

### 3. Anthropic Claude (alternative AI provider)

#### What it does

Same role as XAI/Grok — test generation, test debt detection, self-healing
proposals — but using Anthropic's Claude 3.5 Sonnet model.

#### How to get credentials

1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account
2. Navigate to **API Keys** → **Create Key**
3. Copy the key — it begins with `sk-ant-`
4. Add billing/credits if you are on the free tier (Sonnet requires credits)

#### Environment variables to set

```ini
ANTHROPIC_API_KEY=sk-ant-your_api_key_here
```

#### How the connection works

```python
from anthropic import Anthropic

client = Anthropic(api_key="sk-ant-your_key")
message = client.messages.create(
    model="claude-3-5-sonnet-20240620",
    max_tokens=4096,
    system="You are a senior QA engineer...",
    messages=[{"role": "user", "content": "Analyze this PR diff..."}]
)
test_json = message.content[0].text
```

The `anthropic` package is already in `requirements.txt`. `AIBrainService`
validates the key format (must start with `sk-ant-`), then initialises the
client.

---

### 4. GitHub (webhook source + output target)

#### What it does

GitHub is the trigger and the output destination:
- **Trigger**: a PR opened/updated event is sent to Ghost QA via a webhook
- **Output**: Ghost QA posts a risk report comment on the PR and sets the commit status (green/red check)

#### How to get credentials

**Webhook secret:**
1. Go to your GitHub repository → **Settings → Webhooks → Add webhook**
2. Set **Payload URL** to `https://your-server:8000/api/webhooks/github`
3. Set **Content type** to `application/json`
4. Set **Secret** to a random string (e.g. `openssl rand -hex 32`) — copy it
5. Under **Which events?** select **Pull requests**

**Personal Access Token (for posting comments and status):**
1. Go to GitHub → **Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Select scopes: `repo` (full control of private repositories) — or `public_repo` for public repos only
4. Copy the token — it begins with `ghp_`

#### Environment variables to set

```ini
GITHUB_WEBHOOK_SECRET=your_random_secret_string
GITHUB_TOKEN=ghp_your_personal_access_token
```

#### How the connection works

```
GitHub                              Ghost QA
   │                                    │
   │  PR opened/updated                 │
   │  POST /api/webhooks/github         │
   │  X-Hub-Signature-256: sha256=...   │
   │  X-GitHub-Event: pull_request      │
   │ ─────────────────────────────────► │
   │                                    │ verify HMAC-SHA256 signature
   │                                    │ extract PR info (diff URL, files, commit SHA)
   │                                    │ create PipelineRun record
   │                                    │ spawn background thread
   │ ◄──── {"status": "pipeline_started"│
   │                                    │
   │  (pipeline runs asynchronously)    │
   │                                    │ fetch diff from diff_url
   │  GET /repos/{owner}/{repo}/pulls/  │
   │       {pr_number}/files            │ ◄─── list changed files
   │                                    │
   │  (after tests run...)              │
   │                                    │
   │  POST /repos/{owner}/{repo}/issues/│
   │       {pr_number}/comments         │ ◄─── post risk report comment
   │                                    │
   │  POST /repos/{owner}/{repo}/       │
   │       statuses/{commit_sha}        │ ◄─── set commit status
   │  {"state":"success"|"failure",     │
   │   "context":"Ghost QA"}            │
```

The webhook signature is verified using HMAC-SHA256. If `GITHUB_WEBHOOK_SECRET`
is empty in `.env`, verification is skipped (useful for local testing with ngrok).

#### Exposing your local server to GitHub (development)

GitHub needs a public URL to send webhooks. Use the included `ngrok.exe`:

```bash
# In one terminal — start Ghost QA
python run.py

# In another terminal — expose it
tools\ngrok.exe http 8000
# ngrok gives you: https://abc123.ngrok.io
```

Set the webhook Payload URL to `https://abc123.ngrok.io/api/webhooks/github`.

---

### 5. Slack (notifications)

#### What it does

Slack receives pipeline completion summaries and SLA warning alerts. Each
message shows the repository, PR number, pass/fail counts, risk level, and
recommendation. SLA warnings tell the team when a test approval is overdue.

#### How to get credentials

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App → From scratch**
2. Give it a name (e.g. `Ghost QA`) and pick your workspace
3. Go to **OAuth & Permissions** → add these **Bot Token Scopes**: `chat:write`, `chat:write.public`
4. Click **Install to Workspace** → **Allow**
5. Copy the **Bot User OAuth Token** — it begins with `xoxb-`
6. Invite the bot to your alert channel: in Slack type `/invite @GhostQA`

#### Environment variables to set

```ini
SLACK_BOT_TOKEN=xoxb-your_bot_token_here
SLACK_CHANNEL=ghost-qa-alerts
```

#### How the connection works

```python
import requests

requests.post(
    "https://slack.com/api/chat.postMessage",
    headers={
        "Authorization": "Bearer xoxb-your_token",
        "Content-Type": "application/json"
    },
    json={
        "channel": "ghost-qa-alerts",
        "username": "Ghost QA",
        "icon_emoji": ":ghost:",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "👻 Pipeline Complete"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Risk:* 🔴 CRITICAL\n..."}}
        ]
    }
)
```

`SlackService` in `app/services/slack.py` handles this. When `DEMO_MODE=true`
or `SLACK_BOT_TOKEN` is not set, all Slack calls are logged to the console
instead of sent.

---

### 6. Full end-to-end flow — all integrations together

Here is the complete picture of every system involved in one Ghost QA pipeline
run, from PR push to final result:

```
Developer pushes code
        │
        ▼
   GitHub PR opened
        │
        │  Webhook POST (HMAC-signed)
        ▼
┌───────────────────────────────────────────────────────┐
│  Ghost QA  (FastAPI on port 8000)                     │
│                                                        │
│  1. Verify GitHub webhook signature                    │
│  2. Create PipelineRun (status=extracting)             │
│  3. Fetch PR diff + changed files from GitHub API      │
│                                                        │
│  4. Call XAI Grok / Claude API                         │◄─── XAI or Anthropic
│     → Generate 5 test cases from diff                  │
│     → Detect test debt                                 │
│                                                        │
│  5. Save test cases (status=awaiting_approval)         │
│                                                        │
│  6a. DEMO_MODE=true → auto-approve                     │
│  6b. DEMO_MODE=false → create UiPath Action Center task│◄─── UiPath Action Center
│      Start SLA timer (4h warning / 24h auto-reject)   │
│                                                        │
│  7. Human approves in Action Center                    │
│     (or auto-approved in demo mode)                    │
│                                                        │
│  8. DEMO_MODE=true → MockExecutor (random pass/fail)   │
│  8. DEMO_MODE=false → UiPathExecutor:                  │
│     a. Authenticate with UiPath Cloud (OAuth2)         │◄─── UiPath Cloud
│     b. Upload XAML test file to Test Manager           │
│     c. Create test set                                 │
│     d. Trigger execution on robot                      │
│     e. Poll for result every 10 seconds                │
│     f. Extract outcome + screenshot URL                │
│                                                        │
│  9. RiskEngine: calculate risk level                   │
│     LOW / MEDIUM / HIGH / CRITICAL                     │
│                                                        │
│  10. Failing tests with selector/API/assertion errors  │
│      → Call XAI Grok / Claude                          │◄─── XAI or Anthropic
│      → Propose self-healing fix                        │
│      → Store heal proposal (human reviews separately)  │
│                                                        │
│  11. Post risk report comment to GitHub PR             │◄─── GitHub
│      Set commit status (green ✓ or red ✗)             │
│                                                        │
│  12. Send pipeline summary to Slack                    │◄─── Slack
│                                                        │
└───────────────────────────────────────────────────────┘
        │
        ▼
  Dashboard (/dashboard)
  Report page (/report/{run_id})
```

---

### 7. Priority order for getting started

If you want to go from demo mode to fully live, do it in this order:

| Step | What to do | Unlocks |
|------|-----------|---------|
| 1 | Get `XAI_API_KEY` (free tier available) | Real AI test generation |
| 2 | Get `GITHUB_TOKEN` + configure webhook | Real PR triggering |
| 3 | Get `SLACK_BOT_TOKEN` | Real notifications |
| 4 | Get UiPath Community account + OAuth client | Real test execution |
| 5 | Configure UiPath environments + Action Center catalog | Human approval gate |

Steps 1–3 take under 30 minutes combined and get you 80% of the production
experience. Step 4–5 require UiPath setup time but the mock executor already
demonstrates the full flow in demo mode.

#### Minimal `.env` for real AI + real GitHub (no UiPath yet)

```ini
DEMO_MODE=false
AUTO_APPROVE=true

DATABASE_URL=sqlite:///./ghost_qa.db

# AI (pick one or both — XAI takes priority)
XAI_API_KEY=xai-your_key
GROK_MODEL=grok-4-1-fast-reasoning
# ANTHROPIC_API_KEY=sk-ant-your_key   # fallback if XAI not set

# GitHub
GITHUB_TOKEN=ghp_your_token
GITHUB_WEBHOOK_SECRET=your_random_secret

# Slack (optional)
SLACK_BOT_TOKEN=xoxb-your_token
SLACK_CHANNEL=ghost-qa-alerts

# Security
SECRET_KEY=change_this_to_a_random_64_char_string
```

With this config, UiPath calls fall back to the mock executor automatically —
you get real AI-generated tests, real GitHub PR comments, and real Slack
notifications, while test execution is simulated until you add UiPath credentials.

#### Full production `.env` (all integrations active)

```ini
DEMO_MODE=false
AUTO_APPROVE=false          # require human approval via Action Center

DATABASE_URL=postgresql://ghost_qa:password@localhost:5432/ghost_qa

# AI
XAI_API_KEY=xai-your_key
GROK_MODEL=grok-4-1-fast-reasoning

# GitHub
GITHUB_TOKEN=ghp_your_token
GITHUB_WEBHOOK_SECRET=your_random_secret

# UiPath
UIPATH_CLIENT_ID=your_client_id
UIPATH_CLIENT_SECRET=your_client_secret
UIPATH_TENANT_NAME=your_tenant
UIPATH_ORG_ID=your_org
UIPATH_ENVIRONMENT_ID=your_env_uuid
UIPATH_TEST_FOLDER=GhostQA
UIPATH_EXECUTION_TIMEOUT_SECONDS=300

# Slack
SLACK_BOT_TOKEN=xoxb-your_token
SLACK_CHANNEL=ghost-qa-alerts

# Security
SECRET_KEY=change_this_to_a_random_64_char_string
JWT_EXPIRY_MINUTES=60
AUTH_USERS={"admin@yourcompany.com": {"password_hash": "bcrypt_hash_here", "role": "approver"}}

# Rate limiting
RATE_LIMIT_ENABLED=true
```
