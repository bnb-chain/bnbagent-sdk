# Budget 校验：Agent 拒绝低于约定价格的 Job

## 背景

Client 和 Agent 通过 `/negotiate` 协商后，Agent 返回 `price`。Client 之后应该按照约定价格调用 `setBudget(job_id, agreed_price)` + `fund()` 在链上锁定资金。但是 **目前没有任何机制阻止 Client 设置一个低于协商价格的 budget**。当前 `verify_job()` 只检查：

- Job 存在
- 状态是 FUNDED
- 自己是 provider
- 没过期

**不检查 budget 金额。** 所以 Agent 会傻傻地处理低价 job。

**ERC-8183 合约层面：没有 provider 端的 `reject()` 函数。** 只有 client 和 evaluator 可以 reject。Provider 唯一的办法就是 **不 submit，让 job 自然过期**（client 可以在 `expiredAt` 之后调用 `claimRefund()` 拿回退款）。

## 术语对齐

### ERC-8183 标准定义的术语

| 术语 | 含义 |
|---|---|
| `budget` | Job 的托管金额（**client 端概念**） |
| `setBudget(jobId, amount)` | Client 设置 job 的 budget |
| `paymentToken` | 支付用的 ERC-20 代币 |
| `platformFeeBP` / `evaluatorFeeBP` | 平台/评估者手续费（basis points） |

**注意：`minBudget` 不属于 ERC-8183 标准。** 它是合约实现自己加的。由于 bond 现在由 APEXEvaluator 出，`minBudget` 和 `min_service_fee` 不再需要，应从 SDK 中移除。

### 业界调研：provider 服务报价的命名

| 协议 | 命名 | 模式 |
|---|---|---|
| Livepeer | `pricePerPixel` | provider 的单位报价 |
| Filecoin | `StorageAsk.Price` | provider 发布的报价（ask/bid 模型） |
| Thirdweb | `pricePerToken` | 固定单价 |
| The Graph | `queryFee` | 按次计费 |
| Ocean Protocol | `fixedRate` / `providerFee` | 费率 / 固定费用 |
| OpenAI / Anthropic | `price per token` | 按量计价 |

**共识：`price` 是 provider 服务报价最主流的命名。`budget` 是 client 的托管金额。`fee` 是平台/中间方抽成。三个概念不应混用。**

### SDK 命名统一：统一为 `service_price`

SDK 中同一个值目前有三个名字，违反"如无必要勿增实体"原则：

| 位置 | 当前名字 | 统一后 |
|---|---|---|
| `APEXConfig` | `agent_price` | → `service_price` |
| `NegotiationHandler` | `base_price` | → `service_price` |
| 协商 terms (`TermSpecification`) | `price` | 不变（协议数据结构字段，有 `terms.` 上下文限定） |
| 环境变量 | `AGENT_PRICE` | → `SERVICE_PRICE` |

**统一为 `service_price`**：自描述（"agent 的服务价格"），比裸 `price` 更清晰，不会与 `TermSpecification.price`（协议数据格式字段）混淆。

**`NegotiationTerms.agreed_price` 保留不变** — 它是 service record 里 per-job 的历史记录（"本次协商最终商定的价格"），用于争议仲裁证据，与 `service_price`（agent 全局配置）语境不同。

统一后语义清晰，各概念各归其位：
- **`service_price`** = agent 提供服务的报价（agent 端配置）
- **`budget`** = client 为 job 锁定的金额（client 端 / ERC-8183 概念）
- **`fee`** = 平台或评估者的手续费（ERC-8183: `platformFeeBP`, `evaluatorFeeBP`）
- **`agreed_price`** = 特定 job 协商后商定的价格（service record，per-job 历史记录）

验证逻辑：**`budget >= service_price`**

数据流：
```
env SERVICE_PRICE → APEXConfig.service_price → NegotiationHandler(service_price=...)
                                               → APEXJobOps(service_price=...)
                                                 → verify_job() 检查 budget >= service_price
```

### 清理：移除 `min_service_fee`

由于 bond 现在由 APEXEvaluator 出（不再从 budget 中扣取），`min_service_fee` 概念不再需要。需要移除：

- `NegotiationHandler.__init__()` 的 `min_service_fee` 参数
- `NegotiationHandler.from_apex_client()` 中 `apex_client.min_service_fee()` 的调用
- `NegotiationHandler.min_service_fee` property
- `PriceTooLowError` 异常类
- 相关的 `validate_price` 参数和校验逻辑

## 方案：在 SDK 层 `verify_job()` 中加 budget 校验

在 `APEXJobOps` 上新增一个 `service_price` 参数。`verify_job()` 会将链上 `job["budget"]` 与 `service_price` 做比较。如果 `budget < service_price`，返回 `valid: False`，job 在 `run_job_loop()` 中被 skip，并打印明确的 warning log。

### 为什么放在 `verify_job()` 而不是 `run_job_loop()`

- `verify_job()` 是统一的校验入口，所有检查都应该集中在这里
- 它还被 HTTP endpoint `/job/{id}/verify` 和 middleware 调用，这样外部调用者也能得到 budget 保护
- `run_job_loop()` 已经会 skip `valid: False` 的 job，不需要改

### 为什么把 `service_price` 放在 `APEXJobOps` 上

- `verify_job()` 被多个地方调用（middleware、job loop、HTTP endpoint），给每个调用者都传参数太侵入
- `APEXJobOps` 是正确的归属：它负责所有验证逻辑，且初始化一次就够了

### 防止重复 skip 刷日志

`run_job_loop()` 每 10 秒轮询一次。budget 不足的 job 状态一直是 FUNDED（`setBudget` 只能在 OPEN 阶段调用，FUNDED 后不可改），会被反复发现 → 反复 verify → 反复 reject → 日志刷爆。

解决：在 `run_job_loop()` 中维护一个 `skipped_jobs: set[int]`。verify 失败后将 job_id 加入。下次轮询发现同一 job 时直接跳过，不再 verify 和 log。进程重启后 set 清空（安全，因为会重新 verify 一次）。

```python
skipped_jobs: set[int] = set()

while True:
    ...
    for job in result.get("jobs", []):
        job_id = job["jobId"]
        if job_id in skipped_jobs:
            continue

        verification = await job_ops.verify_job(job_id)
        if not verification["valid"]:
            skipped_jobs.add(job_id)
            # 触发 on_job_skipped 回调（如果提供）
            ...
            continue

        # 正常处理
        ...
```

## 具体改动

### 涉及的文件

命名统一（`agent_price` / `base_price` → `service_price`）：
- `bnbagent/apex/config.py` — 字段和 env var
- `bnbagent/apex/negotiation.py` — 参数名、内部变量、docstring、错误信息
- `bnbagent/apex/server/routes.py` — config 引用
- `bnbagent/config.py` — env mapping
- `bnbagent/apex/README.md` — 文档
- `README.md` — 文档
- `examples/agent-server/src/service.py` — config 引用
- `tests/test_apex_config.py` — 测试
- `tests/test_negotiation.py` — 测试

清理 min_service_fee：
- `bnbagent/apex/negotiation.py` — 移除 `min_service_fee` 参数、`PriceTooLowError`、`validate_price` 逻辑
- `tests/test_negotiation.py` — 移除相关测试

Budget 校验：
- `bnbagent/apex/server/job_ops.py` — `APEXJobOps.__init__()` 加 `service_price`，`verify_job()` 加检查，`run_job_loop()` 加 `skipped_jobs` set 和 `on_job_skipped` 回调

on_job_skipped 回调：
- `bnbagent/apex/server/job_ops.py` — `run_job_loop()` 加 `on_job_skipped` 参数
- `bnbagent/apex/server/routes.py` — `create_apex_app()` 加 `on_job_skipped` 参数并传递

### 1. 命名统一

`bnbagent/apex/config.py`:
```python
# 之前
agent_price: str = "1000000000000000000"
# 之后
service_price: str = "1000000000000000000"
```

`from_env()` 中环境变量：
```python
# 之前
agent_price=get_env("AGENT_PRICE", "1000000000000000000"),
# 之后
service_price=get_env("SERVICE_PRICE", "1000000000000000000"),
```

`bnbagent/apex/negotiation.py`（NegotiationHandler）:
```python
# 之前
def __init__(self, base_price: str, currency: str, ...):
    self._base_price = base_price
# 之后
def __init__(self, service_price: str, currency: str, ...):
    self._service_price = service_price
```

### 2. 移除 `min_service_fee` 相关代码

`bnbagent/apex/negotiation.py`:
- 移除 `PriceTooLowError` 类
- `NegotiationHandler.__init__()` 移除 `min_service_fee`、`validate_price` 参数及校验逻辑
- `NegotiationHandler.from_apex_client()` 移除 `apex_client.min_service_fee()` 调用
- 移除 `min_service_fee` property

### 3. `bnbagent/apex/server/job_ops.py` — Budget 校验

`APEXJobOps.__init__()` 新增 `service_price: int = 0`：

```python
def __init__(
    self,
    rpc_url: str,
    erc8183_address: str,
    private_key: str = "",
    storage_provider: StorageProvider | None = None,
    chain_id: int = 97,
    wallet_provider: WalletProvider | None = None,
    service_price: int = 0,
):
    ...
    self._service_price = service_price
```

`verify_job()` 在过期检查之后加入 budget 检查：

```python
if self._service_price > 0:
    job_budget = job_result.get("budget", 0)
    if job_budget < self._service_price:
        return {
            "valid": False,
            "error": (
                f"Job budget ({job_budget}) is below agent's "
                f"service price ({self._service_price})"
            ),
            "error_code": 402,
            "service_price": str(self._service_price),
        }
```

### 4. `run_job_loop()` — skipped_jobs set + on_job_skipped 回调

```python
async def run_job_loop(
    job_ops: APEXJobOps,
    on_job: Callable[..., Any],
    poll_interval: int = 10,
    metadata: dict[str, Any] | None = None,
    on_job_skipped: Callable[[dict, str], Any] | None = None,
) -> None:
    ...
    skipped_jobs: set[int] = set()

    while True:
        ...
        for job in result.get("jobs", []):
            job_id = job["jobId"]
            if job_id in skipped_jobs:
                continue

            verification = await job_ops.verify_job(job_id)
            if not verification["valid"]:
                skipped_jobs.add(job_id)
                reason = verification.get("error", "unknown")
                logger.warning(f"[JobRunner] Job #{job_id} skipped: {reason}")
                if on_job_skipped:
                    try:
                        if inspect.iscoroutinefunction(on_job_skipped):
                            await on_job_skipped(job, reason)
                        else:
                            await asyncio.to_thread(on_job_skipped, job, reason)
                    except Exception as e:
                        logger.error(f"[JobRunner] on_job_skipped callback error: {e}")
                continue

            # 正常处理 ...
```

### 5. `create_apex_app()` — 传递 on_job_skipped

```python
def create_apex_app(
    config: APEXConfig | None = None,
    on_job: Callable[..., Any] | None = None,
    on_job_skipped: Callable[[dict, str], Any] | None = None,
    ...
) -> FastAPI:
```

传递到 `run_job_loop()`：

```python
run_job_loop(
    job_ops=state.job_ops,
    on_job=on_job,
    poll_interval=effective_interval,
    metadata=task_metadata,
    on_job_skipped=on_job_skipped,
)
```

### 6. `create_apex_state()` — 传递 service_price

```python
job_ops = APEXJobOps(
    rpc_url=config.effective_rpc_url,
    erc8183_address=config.effective_erc8183_address,
    storage_provider=storage,
    chain_id=config.effective_chain_id,
    wallet_provider=config.wallet_provider,
    service_price=int(config.service_price),
)

negotiation_handler = NegotiationHandler(
    service_price=config.service_price,
    currency=config.effective_payment_token,
)
```

### 7. 不需要改动的地方

- `APEXMiddleware` — 内部调用 `verify_job()`，自动得到 budget 保护
- `TermSpecification.price` — 协议数据结构，不变
- `NegotiationTerms.agreed_price` — service record per-job 历史记录，语境不同，不变
- 合约层 — 当前无法改（没有 provider reject），后续如果 ERC-8183 增加 `providerReject()` 可以再扩展

## UX 改进：让 Client 能感知到 budget 不足

上述方案保护了 Agent，但 Client 端体验不好 — job 被 skip 后 Client 收不到任何通知，只能看到 job 一直停在 FUNDED 状态，直到过期才能 `claimRefund()`。

由于合约没有 `providerReject()`，Agent 无法主动改链上状态。但 SDK 层可以通过以下改进让信息更透明：

### 改进 1：`/job/{id}/verify` 返回 `service_price`

Client 主动查询时，不只看到 "budget too low"，还能看到具体差多少：

```json
{
  "valid": false,
  "error": "Job budget (5000000...) is below agent's service price (20000000...)",
  "error_code": 402,
  "service_price": "20000000000000000000"
}
```

Client 知道该设多少，可以创建新 job 重试。改动很小：已包含在 `verify_job()` 的返回值中。

### 改进 2：`/status` 展示定价信息

当前 `/status` 只返回 `agent_address` 和 `erc8183_address`。加上 pricing，Client 不用 negotiate 就能看到价格：

```json
{
  "agent_address": "0x...",
  "erc8183_address": "0x...",
  "service_price": "20000000000000000000",
  "currency": "0xc70B..."
}
```

改动：`routes.py` 中 `/status` endpoint 从 `APEXState` 读取 config 信息。

### 改进 3：Agent 端 `on_job_skipped` 回调

Agent 开发者可以接入自己的通知逻辑。**所有 verify 失败都会触发**，reason 字段清晰给出原因：

```python
def my_skip_handler(job: dict, reason: str):
    # reason 示例：
    # - "Job budget (5000...) is below agent's service price (20000...)"
    # - "Job status is SUBMITTED, expected FUNDED"
    # - "This agent is not the provider for this job"
    # - "Job has expired"
    # - "Failed to fetch job: timeout"
    notify_slack(f"Skipped job #{job['jobId']}: {reason}")

app = create_apex_app(
    on_job=process_task,
    on_job_skipped=my_skip_handler,
)
```

已包含在 `run_job_loop()` 的改动中。配合 `skipped_jobs` set，每个 job 只会触发一次回调，不会重复通知。

## 验证方式

1. 单元测试：`APEXJobOps` 设 `service_price=10**18`，mock 一个 budget 为 `5*10**17` 的 job → 期望 `valid: False, error_code: 402`
2. 单元测试：同上设置，budget `2*10**18` → 期望 `valid: True`
3. 单元测试：`service_price=0`（默认值）→ budget 检查不生效，向后兼容
4. 单元测试：`run_job_loop` 中同一个 job 第二次轮询时被 skip，不触发 verify 和回调
5. 单元测试：`on_job_skipped` 回调被正确调用，reason 字符串包含具体原因
6. 跑现有测试：`python -m pytest tests/` 确保无回归
