# README 改动预览（中文）

以下是各处改动的完整预览，方便你审阅后再执行。

---

## 改动 1: "How APEX Works" 流程图

**当前问题**：流程图只展示链上交互，没有体现 client 怎么触发 agent 执行、怎么拿结果。

**改后流程图**（在 fund 和 submit 之间插入 HTTP 触发步骤）：

```
Client                        Contract (ERC-8183)             Agent (Provider)          Evaluator (UMA OOv3)
  │                               │                               │                         │
  │  1. negotiate() ──────────────┼───────────────────────────►   │                         │
  │     (agree on price & terms)  │                               │                         │
  │                               │                               │                         │
  │  2. create_job() ────────►    │                               │                         │
  │  3. set_budget(price) ───►    │  (use negotiated price)       │                         │
  │  4. fund() ──────────────►    │  (tokens locked in escrow)    │                         │
  │                               │  ─── status: FUNDED ─────►    │                         │
  │                               │                               │  4b. verify: budget ≥    │
  │                               │                               │      service_price?      │
  │                               │                               │                         │
  │  5. POST /job/execute ────────┼───────────────────────────►   │                         │
  │     (trigger execution)       │                               │  5b. verify + execute    │
  │                               │                               │      on_job callback     │
  │  ◄── 200 + result (fast) ─────┼───────────────────────────────│                         │
  │  OR  202 Accepted (slow) ─────┼───────────────────────────────│                         │
  │    → GET /job/{id}/response   │                               │                         │
  │                               │                               │                         │
  │                               │                     submit()  │                         │
  │                               │  ◄────────────────────────────│                         │
  │                               │  ─── auto-trigger hook ──────────────────────────────►  │
  │                               │                               │  6. Assertion initiated  │
  │                               │                               │                         │
  │                               │                               │  7. Liveness (30 min)    │
  │                               │                               │     Anyone can dispute   │
  │                               │                               │                         │
  │  No dispute:                  │                               │                         │
  │                               │  ◄── settle_job() ───────────────────────────────────── │
  │                               │  ─── payment to agent ───►    │  8. COMPLETED            │
  │                               │                               │                         │
  │  If disputed:                 │                               │                         │
  │     UMA DVM vote (~48-72h)    │                               │                         │
  │     ├─ DVM rules FOR  ───►    │  ─── payment to agent ───►    │     COMPLETED            │
  │     └─ DVM rules AGAINST ─►   │  ─── refund to client         │     REJECTED             │
```

**新增关键点**：步骤 5 展示 client 通过 HTTP 触发 agent 执行，短任务直接返回 200 + 结果，长任务返回 202 后用 `/job/{id}/response` 获取。

---

## 改动 2: Option 2 Mount 示例补充 lifespan

**当前问题**：mount 示例缺少 lifespan，用户照着写会丢失启动扫描。

**改后**：

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from bnbagent.apex.server import create_apex_app

def execute_job(job: dict) -> str:
    return f"Processed: {job['description']}"

apex_app = create_apex_app(on_job=execute_job)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await apex_app.state.startup()  # trigger startup scan for pending jobs
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/apex", apex_app)
# APEX routes at /apex/submit, /apex/status, /apex/health, /apex/job/execute, etc.
# Your own routes on app work alongside.
```

> **Note**: Starlette does not propagate lifespan events to mounted sub-apps. You must call `apex_app.state.startup()` explicitly in your parent app's lifespan to enable the startup scan.

---

## 改动 3: "What you get" — 接入后获得的能力

**当前问题**：line 287 只简略说 "handles everything internally"，用户不清楚具体获得什么。

**在 Option 1 示例后面新增**：

> **What `create_apex_app()` sets up for you:**
>
> - **Wallet management** — auto-creates or loads an encrypted keystore (Keystore V3)
> - **Startup scan** — on boot, batch-scans all on-chain jobs via Multicall3 and auto-processes any funded jobs assigned to you
> - **`POST /job/execute`** — client-triggered execution with configurable timeout: returns 200 with full result (fast jobs) or 202 Accepted (background processing)
> - **`POST /negotiate`** — off-chain price negotiation with on-chain hash anchoring
> - **`POST /submit`** — verifies job on-chain, uploads deliverable to IPFS, submits content hash on-chain (auto-triggers evaluator assertion)
> - **`GET /job/{id}/response`** — clients fetch completed deliverables
> - **`GET /status`** — exposes service price and agent info so clients know the minimum budget
> - **Budget protection** — automatically rejects underpaid jobs where `budget < service_price`

---

## 改动 4: "Job Execution" section 优化

**当前位置**：README.md line 607-616

**修改**：删掉 "This replaces the former background polling loop..." 这句（新用户不需要知道旧架构），保留两个执行路径的描述。

改后：

> ### Job Execution
>
> When you pass `on_job` to `create_apex_app()`, the SDK enables two execution paths:
>
> 1. **Startup scan** — on application boot, a one-time Multicall3 batch scan discovers all pending funded jobs and processes them automatically.
> 2. **Client-driven `POST /job/execute`** — after funding a job, the client calls `/job/execute` to trigger immediate execution. If the job completes within `job_timeout` seconds (default 120), the response includes the full result (200). Otherwise the server returns 202 Accepted and the job continues in the background — the client can poll `GET /job/{id}/response` for the result.
>
> If you're adding APEX to an existing app via sub-app mount, the parent app should call `apex_app.state.startup()` during its own lifespan to trigger the startup scan (Starlette does not propagate lifespan events to mounted sub-apps). See [Option 2](#option-2-mount-on-existing-app-sub-app).

---

## 改动 5: Security 段修正

**当前**：line 756 `"This check runs in the job loop and submit pre-check."`

**改后**：`"This check runs in the startup scan, /job/execute, and submit pre-check."`

---

## 改动 6: APEX README mount 示例补充 lifespan

**文件**：`bnbagent/apex/README.md` lines 44-49

**改后**：

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from bnbagent.apex.server import create_apex_app

apex_app = create_apex_app(on_job=execute_job)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await apex_app.state.startup()
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/apex", apex_app)
```

---

## 改动 7: Examples 过时引用

### 7a. `examples/getting-started/step3_run_agent.py`

```diff
 Starts a minimal APEX agent server that:
-  - Exposes APEX endpoints (negotiate, submit, job query)
-  - Polls for funded jobs in the background
-  - Automatically processes and submits results
+  - Exposes APEX endpoints (negotiate, submit, job/execute, job query)
+  - Scans for pending funded jobs on startup
+  - Accepts client-driven job execution via /job/execute
```

```diff
-# App — one call does everything: routes + polling + lifecycle
+# App — one call does everything: routes + startup scan + lifecycle
```

### 7b. `examples/agent-server/src/service.py`

```diff
-# App — create_apex_app handles routes, polling, and lifecycle
+# App — create_apex_app handles routes, startup scan, and lifecycle
```

### 7c. `examples/agent-server/README.md`

```diff
-3. Agent polls for funded jobs, searches news, submits results to IPFS
+3. Agent scans for funded jobs on startup, accepts /job/execute requests, submits results to IPFS
```

### 不改的（client 侧轮询，行为正确）：

- `step4_create_job.py` — client 轮询等 agent 提交，正确
- `client-workflow/scripts/run_demo.py` — client 侧轮询，正确
- `test_progressive_simulation.py` — SDK 内部测试，无需改

---

## 修改文件总览

| # | 文件                                            | 改动                                                            |
| - | ----------------------------------------------- | --------------------------------------------------------------- |
| 1 | `README.md`                                   | 流程图 + mount 示例 + "What you get" + Job Execution + Security |
| 2 | `bnbagent/apex/README.md`                     | mount 示例加 lifespan                                           |
| 3 | `examples/getting-started/step3_run_agent.py` | docstring + 注释                                                |
| 4 | `examples/agent-server/src/service.py`        | 注释                                                            |
| 5 | `examples/agent-server/README.md`             | "How It Works" 描述                                             |
