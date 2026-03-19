# Budget Validation + 命名统一 + min_service_fee 清理 — 变更总结

## 概述

本次变更合并三件事：
1. **命名统一**：`agent_price` / `base_price` → `service_price`
2. **移除 `min_service_fee`**：bond 由 APEXEvaluator 出，不再需要
3. **Budget 校验**：`verify_job()` 检查 `budget >= service_price`，配合 `skipped_jobs` set 防刷 + `on_job_skipped` 回调

设计文档：[`docs/design/budget-validation.md`](./budget-validation.md)

---

## 1. 命名统一：`agent_price` / `base_price` → `service_price`

统一后语义清晰，各概念各归其位：
- **`service_price`** = agent 提供服务的报价（agent 端配置）
- **`budget`** = client 为 job 锁定的金额（client 端 / ERC-8183 概念）
- **`agreed_price`** = 特定 job 协商后商定的价格（service record，不变）

环境变量同步更新：`AGENT_PRICE` → `SERVICE_PRICE`

### 改动文件

| 文件 | 改动内容 |
|------|----------|
| `bnbagent/apex/config.py` | 字段 `agent_price` → `service_price`，`from_env()` 读 `SERVICE_PRICE` |
| `bnbagent/config.py` | `BNBAgentConfig.from_env()` 中 `agent_price` / `AGENT_PRICE` → `service_price` / `SERVICE_PRICE` |
| `bnbagent/apex/negotiation.py` | `NegotiationHandler` 参数 `base_price` → `service_price`，内部 `self._base_price` → `self._service_price` |
| `bnbagent/apex/server/routes.py` | `config.agent_price` → `config.service_price`，`NegotiationHandler(base_price=...)` → `NegotiationHandler(service_price=...)` |
| `examples/agent-server/src/service.py` | `config.agent_price` → `config.service_price`，docstring 中 env var 名称 |
| `tests/test_apex_config.py` | 所有 `config.agent_price` → `config.service_price`，`AGENT_PRICE` → `SERVICE_PRICE` |
| `tests/test_negotiation.py` | `base_price=` → `service_price=` |
| `README.md` | 代码示例、env var 表格、`.env` 示例 |
| `bnbagent/apex/README.md` | env var 表格 |

---

## 2. 移除 `min_service_fee` 相关代码

由于 bond 现在由 APEXEvaluator 出（不再从 budget 中扣取），`min_service_fee` 概念不再需要。

### 移除内容

| 文件 | 移除内容 |
|------|----------|
| `bnbagent/apex/negotiation.py` | `PriceTooLowError` 类、`NegotiationHandler.__init__()` 的 `min_service_fee` 和 `validate_price` 参数及校验逻辑、`from_apex_client()` 中 `apex_client.min_service_fee()` 调用、`min_service_fee` property |
| `bnbagent/apex/__init__.py` | `PriceTooLowError` 的 import 和 `__all__` export |
| `tests/test_negotiation.py` | `TestPriceTooLowError` 测试类、`test_price_too_low_at_init` 测试用例、`test_from_apex_client` 中 `min_service_fee` 相关断言 |

### 保留不变

- `APEXClient.min_budget()` — 合约 ABI 方法，通用客户端不应删
- `TermSpecification.price` — 协议数据结构字段
- `NegotiationTerms.agreed_price` — per-job 历史记录，语境不同

---

## 3. Budget 校验

### 3.1 `APEXJobOps` 新增 `service_price`

`bnbagent/apex/server/job_ops.py`：

- `__init__()` 新增 `service_price: int = 0` 和 `payment_token_decimals: int = 18` 参数
- `service_price` 默认值 `0` 表示不检查 budget（向后兼容）

### 3.2 `verify_job()` 新增 budget 检查

在过期检查之后、安全警告之前：

```python
if self._service_price > 0:
    job_budget = job_result.get("budget", 0)
    if job_budget < self._service_price:
        return {
            "valid": False,
            "error": f"Job budget ({job_budget}) is below agent's service price ({self._service_price})",
            "error_code": 402,
            "service_price": str(self._service_price),
            "decimals": self._payment_token_decimals,
        }
```

由于 `verify_job()` 是统一校验入口，以下调用方自动获得 budget 保护：
- `run_job_loop()` 中的 job 轮询
- `/job/{id}/verify` HTTP endpoint
- `APEXMiddleware` 中间件
- `submit_result()` 的 defense-in-depth 检查

### 3.3 `run_job_loop()` 防刷机制

`skipped_jobs: set[int]`：verify 失败的 job 被记录，后续轮询直接跳过，避免日志刷爆。进程重启后 set 清空（安全，会重新 verify 一次）。

### 3.4 `on_job_skipped` 回调

`run_job_loop()` 和 `create_apex_app()` 均新增 `on_job_skipped: Callable[[dict, str], Any] | None` 参数。

- 支持 sync 和 async 回调
- 每个 job 只触发一次（配合 `skipped_jobs` set）
- reason 字符串包含具体原因（budget 不足、已过期、非本 agent 等）

使用示例：

```python
def my_skip_handler(job: dict, reason: str):
    notify_slack(f"Skipped job #{job['jobId']}: {reason}")

app = create_apex_app(
    on_job=process_task,
    on_job_skipped=my_skip_handler,
)
```

### 3.5 数据流

```
env SERVICE_PRICE
  → APEXConfig.service_price
    → create_apex_state()
      → APEXJobOps(service_price=int(...))
        → verify_job(): budget >= service_price?
      → NegotiationHandler(service_price=...)
        → negotiate(): 返回 service_price 给 client
```

---

## 4. UX 改进：`/status` 展示定价 + `decimals`

### 4.1 `APEXConfig` 新增 `payment_token_decimals`

```python
payment_token_decimals: int = 18
```

作为 `decimals` 的数据源，传递到 `APEXJobOps` 和 `/status`。

### 4.2 `/status` endpoint

新增 `service_price`、`currency`、`decimals` 字段：

```json
{
  "status": "ok",
  "agent_address": "0x...",
  "erc8183_address": "0x...",
  "service_price": "20000000000000000000",
  "currency": "0xc70B...",
  "decimals": 18
}
```

Client 不用 negotiate 就能看到 agent 的定价信息，并正确解析金额。

### 4.3 `verify_job()` 402 响应

budget 不足时返回 `decimals`，让 client 理解差额：

```json
{
  "valid": false,
  "error": "Job budget (5000...) is below agent's service price (20000...)",
  "error_code": 402,
  "service_price": "20000000000000000000",
  "decimals": 18
}
```

---

## 测试结果

```
353 passed, 0 failed
```

全部测试通过，无回归。
