# TWAK × bnbagent-sdk 集成设计（最终方案）

> 状态：**Phase 1a–1c 已实施**（commits 928e8e1 / 368c55d / 29cb7f5），等待 twak 上游（续作见 [`twak-next-steps.md`](./twak-next-steps.md)）｜ 日期：2026-06-11 ｜ 作者：SDK 团队（基于 robot-ux b2e85eb 架构延伸）
> 基线：twak CLI **v0.18.0**（命令面唯一事实源：[`docs/twak-cli-gaps-v0.18.0.md`](./twak-cli-gaps-v0.18.0.md)）｜ 2026-06-12 起最低支持 **v0.19.0**（REQ-1/S-1/S-2/S-3/S-4 已上线，守卫①②与 fund 预检退役——见 §8 触发表与 §11）
> 关联仓库：`bnbagent-studio`（部署形态与政策层的兼容性约束来源）
>
> 证据等级标注：**[实测]** 本设计过程中直接验证 ｜ **[gaps]** 团队实测文档 ｜ **[peer]** GitHub 同行调研 ｜ **[代码]** 仓库现状事实

---

## 0. 总览

一句话：**保留 robot-ux 的 Intent 执行架构作为地基，把 twak 建模为"永久受限但边界清晰的执行后端"——能力显式化、绕得开的整段委托（x402）、绕不开的三道闸 fail-fast，twak 补齐能力之日各路径自动点亮。**

```
                         SDK 调用方（facade / agent tools）
                  Phase 1b 起经 capabilities() 路由；装配期过滤（apex 落点待定）
                                         │
        ┌────────────────────────────────┼────────────────────────────────────┐
        │ (A) 合约操作                    │ (B) x402 付款                       │ (C) 裸签名原语
        ▼                                ▼                                    ▼
  Intent(name, kwargs, call)      X402Payer (Protocol)              WalletProvider.sign_*()
  【1a 全量打通】                  quote() / request()                【1b: abstract → 默认抛
        │                         X402Quote / X402PaymentResult       UnsupportedWalletOperation,
  wallet.make_executor()          【1c 契约定稿】                      sign.* 能力自动推导】
  【1b: 入口检查 sign.transaction】       │
        │                         wallet.make_x402_payer(**kw)
        │                         【1c 立缝: 默认=能力闸,
        │                          LocalX402Payer 落地后默认升级】
════════╪════════════════════════════════╪════════════════════════════════════╪═══════════
 EVM    ▼                                ▼                                    ▼
 钱包  LocalExecutor                    【现状】X402Signer 直用                全部 ✓（SigningPolicy 把关）
       build→sign→broadcast            【Phase 3】LocalX402Payer              sign.* + calls.arbitrary
       任意合约 ✓                        （httpx 402 循环 + X402Signer，         + paymaster.sponsor
                                          自 studio buyer.py 上提）
════════╪════════════════════════════════╪════════════════════════════════════╪═══════════
 TWAK   ▼                                ▼                                    ▼
       TWAKProvider（自身即 executor）    TwakX402Payer 【1c】                  sign_message ✓（三件适配）
       【1a】erc8004×3 + erc8183×13      五项预检 → request                    sign_typed_data ✗（四道闸；
       opt_params 透传(v0.19)；守卫:      --max-payment --yes                   不携带任何实现——P0：判定
       paymaster→WARN/未知intent拒绝      → 按报价金额记账                       大概率不会支持，直接抛错）
════════╪═══════════════ (L8 custody, 1c) ══════════════════════════════════════════════════
        │  开发机: .env.local ─load_env()─▶ 进程env ─继承─▶ twak 子进程
        │  部署:   SM bundle ─冷启动─▶ env ─materialize_twak_home()─▶ <home>/.twak/
        │          TWAKProvider(home=HOME覆盖[实测✓], expected_address, auto_create=False)
        │  AgentCore: 必须 deployment_type: container（Node≥20）；direct_code_deploy → REST/NaaS（Phase 3+）
```

### 非目标（本迭代不做）

- LocalX402Payer 实体（与 studio 协调上提 `bnbagent_studio_core/x402/buyer.py`，Phase 3）
- REST / NaaS transport（`_run()` 已收口，第二个 transport 到来时提取接口）
- paymaster 透传（REQ-2 开放）、`make_signer()`（见 P6 触发条件）
- studio 侧 trust kind 接线（移交清单见 §9.4）

---

## 1. 背景

robot-ux 的 b2e85eb 引入了 IntentExecutor 执行缝并接入 TWAK（仅 erc8004 三个 intent）。本设计完成其预留的 8183 接入，并解决三个新问题：

1. **twak 是受限钱包**：只能执行固定命令菜单（8004×5 + 8183×12 条 CLI 命令（13 个 SDK intent，complete/reject 同 reason 形态）+ x402 客户端），无任意交易签名，无通用 EIP-712（v0.18.0 实测无 `sign-typed-data` 命令 [实测]）——`WalletProvider` 四个抽象方法它只能诚实实现一个半；
2. **x402 形态不同**：twak 的 x402 是完整 HTTP 客户端（内签 EIP-3009/Permit2），与 SDK 的签名原语层 `X402Signer` 不在同一高度；
3. **部署形态**：studio 把 keystore 放 AWS Secrets Manager、冷启动物化——twak 的固定路径 custody（`~/.twak/`）必须兼容该模式。

---

## 2. 设计原则（每条带依据）

| # | 原则 | 依据 |
|---|---|---|
| P1 | **高级操作是必选原语，裸签名是可选项**——twak 永不被要求"签东西"，只被要求"做事" | [peer] goat-sdk 接口无 `signTransaction`；thirdweb `sendTransaction` 必选/`signTransaction?` 可选；agentkit SmartWallet 对 signTransaction 抛错、sendTransaction 走 userOp |
| P2 | **调用方零分支**，后端差异由钱包的 `make_*()` 工厂吸收 | [代码] robot-ux make_executor docstring；[peer] viem `account.type: local\|json-rpc` 同构路由 |
| P3 | **能力显式化三道闸**：`capabilities()` 自省 → 装配期过滤 → 运行期描述性报错 | [peer] EIP-5792 getCapabilities；goat supportsChain 装配过滤；agentkit/ethers VoidSigner 描述异常 |
| P4 | **宁可入口报错，不可静默降级** | [gaps] spec 与实际 CLI 已分叉（`{success,txHash}` vs `hash`、`create` vs `create-job`）；[实测] spec 的 `bsc-testnet` 被 CLI 拒绝（CHAIN_UNSUPPORTED）——"按 spec 超前实现"的实锤翻车 |
| P5 | **外部支付方的抽象缝是 "handle payment"，不是 "sign bytes"** | [peer] L402/aperture 委托外部 lnd 十年验证；a2a-x402 executor 委托；x402 官方 `on_payment_required` 逃生门 |
| P6 | **缝 = 组装点，不是层标配**。strategy 缝只立在"有多种做法要选"的地方；只有"能不能"之分的地方用能力位。`make_signer()` 在所有实现里都退化为 `return self`（恒等工厂），不立。触发条件：① 签名长出策略维度（MPC 异步会话/批量签名）；② 出现"只签名不转账"的衰减视图消费者 | 三层 make_* 对比表（§3.3.3）；[代码] (C) 的工厂已存在 = `create_wallet_provider(kind)` 本身 |

补充纪律：**"第二实现纪律"约束契约，不约束选择器**——Protocol 的方法签名需要两个真实实现来蒸馏（X402Payer 由 studio `fetch_with_payment` + `twak x402 request` 蒸馏 [代码+实测]）；选择器（`make_x402_payer`）在能力模型下有合法的单实现形态（实现了的返回、没实现的闸住），可先立。

---

## 3. 架构设计

### 3.1 (A) 合约操作 — Intent 层全量打通【Phase 1a】

- `CommerceClient` / `RouterClient` / `PolicyClient` 全部 13 个写方法构造双表示 `Intent(name, kwargs, call)`，经 `ContractClientMixin._execute_intent()`（懒建缓存 `wallet.make_executor(ExecutionContext(web3))`）执行。
- EVM 路径行为零变化：`LocalExecutor` 与 `_send_tx` 同源（nonce/预检/gas floor/重试）[代码]；mixin 的 `_send_tx` 保留给 erc20 等未迁移调用方。
- `ERC8183Client.fund()`：`wallet.fund_bundles_approval is True` 时跳过 allowance 预审批（twak 的 `fund` 自带 approve+deposit 两笔 [gaps]）。`is True` 写法保证 MagicMock 测试不误触。行为标志留属性、不进能力集（§3.4 粒度纪律）。

> 2026-06-12 更新：下面的分发表与守卫规则保留为 **v0.18.0 时期的设计记录**。twak **v0.19.0**（REQ-1/S-1/S-2 上线）后：守卫①②退役、opt_params 在全部 erc8183 写命令上原样透传（`--opt-params`）、fund 的 status 预检删除改传 `--expected-budget`（合约侧原子 `BudgetMismatch()`）。执行记录见 §8 触发表与 §11。

#### TWAK 分发表（v0.18.0 命令面 1:1）

| Intent | twak 命令 | 备注 |
|---|---|---|
| `erc8004.register` | `register --uri <u> --metadata k=v`（可重复，**原子**） | [gaps] agent 1350/1351 实测；**删除 set-metadata 回放 workaround** |
| `erc8004.set_agent_uri` / `set_metadata` | `set-uri` / `set-metadata` | [gaps] ✅ 双网 |
| `erc8183.create_job` | `create-job --provider --evaluator --expires-at --description [--hook]` | [gaps] job 137/138 |
| `erc8183.set_provider` | `set-provider <jobId> --provider` | v0.18.0 新增（0.16 没有 [实测]）|
| `erc8183.set_budget` | `set-budget <jobId> --amount` | |
| `erc8183.fund` | `fund <jobId>`（无金额参数！approve+deposit 两笔） | **委托前 `status` 预检链上 budget == expected_budget**（S-2 客户端兜底；status JSON 的 budget 是扁平字符串 [实测]）|
| `erc8183.submit` | `submit <jobId> --deliverable <0xhex32>` | **REQ-1 前：带非空 opt_params 即 fail-fast**（见守卫②）|
| `erc8183.complete` / `reject` | `--reason <0xhex32>`（零值省略） | |
| `erc8183.claim_refund` / `register_job` / `settle` / `mark_expired` / `dispute` / `vote_reject` | 同名直映射（`register-job --policy`、`settle --evidence`） | [gaps] 全 ✅ |

#### 守卫规则（P4 落地，全部抛 `UnsupportedWalletOperation`，message = 能力/原因 + 替代路径 + REQ/S 编号；①② 已于 v0.19.0 退役，见上方更新框）

1. 除 submit 外的写操作带非空 `opt_params` → 拒绝，引用 **S-1**（现状 SDK 这 5 处本就只发空值 [代码]，守卫只拦异常用法）；
2. submit 带非空 `opt_params` → 拒绝，引用 **REQ-1**（[gaps] Note 1 链上实证：twak 提交的 job 137 `JobInitialised` optParams 为空 → 不可评估，协议级断裂；SDK facade 的 submit 必带 deliverable_url，故 twak 卖方角色在 REQ-1 前整体不可用——文档明示）；
3. 无语义名/未知 intent → "twak 不能执行任意合约调用"，指向 EVM 钱包路径；
4. `make_executor(context)` 收到非空 paymaster → **WARNING**（REQ-2 开放；[gaps] 实测必须给 twak 钱包预存 BNB——赞助静默变自费是资金可见行为，必须出声）；
5. twak 返回 `unknown command/option` → "升级 twak ≥ v0.18.0"（不再误导查 setup）。

### 3.2 (B) x402 — 委托型支付缝【Phase 1c】

#### 模块布局

```
bnbagent/x402/
├── signer.py      X402Signer            不动（EVM 签名原语 + 交易级守卫）
├── budget.py      SessionBudgetTracker  不动，两路径共享
├── payer.py  新   X402Payer(Protocol): quote(url,*,method,body) / request(url,*,max_payment,...)
│                  X402Quote（字段=实测 accepts 条目）/ X402PaymentResult（支付元数据 Optional）
└── twak.py   新   TwakX402Payer(provider, *, session_budget=None, ...)
```

#### 选择缝（评审反转后采纳，进 1c）

```python
# wallet_provider.py — 默认实现 = 能力闸（与 sign_* 默认抛错同模式，非死代码）
def make_x402_payer(self, **payer_kwargs) -> X402Payer:
    raise UnsupportedWalletOperation(X402_PAY, ...)
    # LocalX402Payer 落地后默认升级为:
    # 有 sign.typed_data 能力 → 返回 LocalX402Payer —— 严格能力增强，非破坏
# kwargs 原样透传（create_wallet_provider 同款约定 [代码]），冻结风险消除

# twak_provider.py — 覆写
def make_x402_payer(self, **kw): return TwakX402Payer(self, **kw)
```

#### TwakX402Payer 行为（全部有实测背书）

1. **quote**：CLI 直映射；**不触发 `_ensure_wallet()`**（F-3：quote 只读无需钱包 [实测]，否则一次报价就可能静默建钱包，违反 INV-4）；
2. **五项预检**（quote → 比对 → request）：`payTo`（收款人）、`asset`（**即 EIP-3009 domain 的 verifyingContract**——和 SigningPolicy 的 domain 白名单是同一个检查，数据来源不同）、`amount ≤ max_payment`、`network`、`maxTimeoutSeconds ≤ 上限`（**F-2：默认 3600s 而非 SigningPolicy 的 600s**——实测 Bazaar 规范端点 onesource=3600s，600 会拒掉合法端点；且 twak 路径上实际签名窗口由 twak 内部决定，本检查只约束 challenge 声称值，风险等级不同，可配）；
3. **request**：`--max-payment --yes --json`，**只解析 stdout**（实测：人类可读横幅 `x402: paying …` 走 stderr，stdout 纯 JSON）；
4. **记账**：按**报价金额**（或 max_payment 上限）喂 `SessionBudgetTracker`——实测成功输出 = 端点响应体透传，**无支付回执元数据**（→ 上游建议 S-7）；
5. TOCTOU（quote→request 之间 twak 重新发现 challenge）：`--max-payment` + `--prefer-*` 收窄兜底，文档化。

#### 守卫三层纵深（与 studio 政策层的分工，[代码] 实读确认）

```
studio 政策层（钱包无关，作用在 URL+USD、调 payer 之前 → trust kind 零改动复用）
  host 白名单（buyer.py:_check_host + [payments.x402].allowed_hosts）
  单日 $20 / topup 场景单笔 $1 · 日 $3 · 月 $15（BudgetTracker/BudgetGate，进程内 UTC 日桶，不持久化）
  注：无显式"单日笔数"闸——由 日额÷单笔额 + 60s cold-start + 失败退避 涌现
       ↓
SDK 原语层（本设计交付）
  TwakX402Payer 五项预检 + SessionBudgetTracker     ←→ EVM 路径: X402Signer(expected_to/max_value/budget)
       ↓
钱包层
  twak --max-payment 硬顶（单笔被执行两次=双保险）   ←→ EVM 路径: SigningPolicy(domain/type/窗口)
```

**SigningPolicy 为什么不移植到 twak 路径**：它审计签名前的 712 payload，而 twak 路径上 payload 在 twak 进程内构造/签名/销毁——检查材料不过境。但每条规则的**语义等价物**都已落在 quote 条款预检上（domain↔asset、primaryType↔命令菜单固化、窗口↔maxTimeoutSeconds）。防御从"签字节前审计"位移为"委托前审计条款"。

**twak 内建限制面**（[实测] grep CLI bundle）：只有 `maxPayment`（单次）——无 daily/monthly/count/host 配置。需向 twak 团队问询 NaaS 端是否有/计划有 server-side spending policy。

### 3.3 (C) 签名原语 — 能力门控的接口本体【Phase 1b】

#### 3.3.1 方法降级 + 自动推导

- `sign_message` / `sign_transaction` / `sign_typed_data` 从 `@abstractmethod` 降为**默认实现抛 `UnsupportedWalletOperation`**；只有 `address` 保持 abstract；
- 基类 `capabilities()` 从"方法是否被覆写"自动推导 `sign.*`（声明与行为不可漂移）；
- 纪律：**不支持就不要覆写**（覆写即宣告能力）——MPC stub 的三个抛错方法删除；
- `make_executor()` 默认实现入口检查 `sign.transaction`（错误提前到构造点，C-3）。

#### 3.3.2 twak 的 (C) 列调用链

```
sign_message (EIP-191) — twak 支持 ✓ — 现有消费者全覆盖:
  negotiation.provider_sig、pieverse SIWE 绑定（x402 归账前提，实测响应原话
  "signer wallet must be SIWE-bound for attribution"）
  适配三件: ① 0x 前缀归一（S-4）② 客户端算 EIP-191 digest（接口契约要求）
           ③ ecrecover 自校验: 恢复地址≠钱包地址即抛 —— 自算 digest 配外部签名，
             必须有运行期闭环证明两边对消息字节的理解一致（防编码分歧延迟爆炸）

sign_typed_data (EIP-712) — twak 不支持 ✗ — 四道闸:
  闸0 装配期  capabilities() 无 sign.typed_data → 工具不进 LLM 列表
  闸1 组合期  X402Signer.__init__ 检查 supports(SIGN_TYPED_DATA) → 立即拒绝并指向 make_x402_payer()
             （getattr 兜底: 鸭子类型对象无 supports 则放行，运行期闸接手）
  闸2 运行期  直调 → 基类默认实现直接抛 UnsupportedWalletOperation（不发起任何 CLI 调用——P0）
  正确出路   x402 走 (B) 列整段委托 —— SDK 里 712 的唯一真实消费者就是 x402 [代码]
  不可桥接   用 191 模拟 712 密码学上封死（digest 前缀不同, ecrecover 必失败）
```

**已知限制（诚实交代）**：窄协议是结构化匹配，`sign_typed_data` 方法在基类存在（默认抛错）→ mypy 拦不住 `X402Signer(twak)`。考虑过从基类删方法（真 ISP，静态可拦），否决：破坏 studio 静态层（`_build_wallet` 返回 `WalletProvider` 直传 X402Signer [代码]）+ 失去描述性报错。运行期三道闸承重——thirdweb/agentkit 现役同款。

#### 3.3.3 为什么 (C) 没有 `make_signer()`（P6 的推导）

| 缝 | EVM 返回 | TWAK 返回 | 外部材料 | 型号选择 |
|---|---|---|---|---|
| `make_executor()` | `LocalExecutor(web3, wallet, paymaster)` 新对象 | `self` | ✓ | ✓ |
| `make_x402_payer()` | `LocalX402Payer(wallet, http, guards)` 新对象 | `TwakX402Payer(self)` | ✓ | ✓ |
| 假想 `make_signer()` | `self` | `self` | ✗ | ✗ —— **恒等工厂** |

(A)/(B) 的 make_* 是在钱包之上**组装机器**（签名+广播流程 / 签名+HTTP流程）；(C) 里钱包本身就是产品——**(C) 的工厂已经存在，名字叫 `create_wallet_provider(kind)`**。

#### 3.3.4 消费方窄协议（接口隔离，纯 typing，零运行时对象）

```python
# wallets/protocols.py
class MessageSigner(Protocol):    # negotiation 的真实依赖
    @property
    def address(self) -> str: ...
    def sign_message(self, message: str) -> dict[str, Any]: ...

class TypedDataSigner(Protocol):  # X402Signer 的真实依赖
    @property
    def address(self) -> str: ...
    def sign_typed_data(self, domain, types, message) -> dict[str, Any]: ...
```

形状即 coinbase x402 v2 的 `ClientEvmSigner`（[peer] 原文 "deliberately tiny, structural — the entire wallet contract for the buyer"）。能力位 ↔ 窄协议 1:1（`sign.message`↔`MessageSigner`，`sign.typed_data`↔`TypedDataSigner`）。第三方鸭子对象可直接接入 X402Signer，无需继承 WalletProvider。

### 3.4 能力集设计【Phase 1b】

```python
# bnbagent/wallets/capabilities.py — 开放字符串注册表（非 Enum）
SIGN_MESSAGE      = "sign.message"        # ← 自动推导
SIGN_TRANSACTION  = "sign.transaction"    # ← 自动推导; LocalExecutor 前提
SIGN_TYPED_DATA   = "sign.typed_data"     # ← 自动推导; X402Signer 前提
CALLS_ARBITRARY   = "calls.arbitrary"     # 任意合约机械调用 —— 把"twak 只能固定合约"编码为一个能力位
BROADCAST_SELF    = "broadcast.self"
INTENTS_ERC8004   = "intents.erc8004"
INTENTS_ERC8183   = "intents.erc8183"
X402_PAY          = "x402.pay"            # SDK 能替你完成 x402 支付（1c 仅 twak; EVM 等 LocalX402Payer）
PAYMASTER_SPONSOR = "paymaster.sponsor"
```

规则：① **开放集**——第三方 vendor 命名空间扩展不动核心；消费者**未知忽略、缺席即不支持**（EIP-5792 同款）；② **维度正交**——sign.*/执行面/服务面互不蕴含；③ **防漂移**——sign.* 自动推导 + 每 provider 一致性测试（声明的必可用、未声明的必抛 `UnsupportedWalletOperation`）+ "覆写即能力"反例断言；④ **粒度纪律**——能力 = 影响路由的"能不能"；行为差异（`fund_bundles_approval`）留属性。

| Provider | capabilities() |
|---|---|
| EVM | `sign.message, sign.transaction, sign.typed_data, calls.arbitrary, paymaster.sponsor` |
| TWAK | `sign.message, broadcast.self, intents.erc8004, intents.erc8183, x402.pay` |

`describe()` 增加 `capabilities` 字段；`supports(cap)` 便捷方法。

### 3.5 transport 问题（CLI vs 进程内）

公开层的抽象就是 `WalletProvider` ABC 本身（隐藏"怎么做到"是它的本职；[peer] CDP `toAccount` 把网络往返包进同形接口、viem json-rpc account 藏 transport——没有谁把 transport 放进钱包公开抽象）。TWAKProvider 内部已有唯一收口点 `_run(args)->dict`；**不升格为公开接口**——transport 接口是契约，需要第二个 transport（REST/NaaS，gaps 承诺 CLI/MCP parity）来蒸馏，届时从 `_run` 提取是机械重构。

---

## 4. 密码与存储（L8）【Phase 1c】

### 4.1 不变量

- **INV-1** 密码永不进 argv（`ps` 全机可见；robot-ux 不变量保留）
- **INV-2** env 是密码唯一载体（subprocess 默认继承 → 进程内存直达，无落点）
- **INV-3** 密钥材料只以加密形态流转，落盘 0700/0600
- **INV-4** 部署环境禁止隐式建钱包（studio 身份锚定教训 [代码]：静默新建 = 链上身份漂移）
- **INV-5** 日志/报错脱敏（env 键名白名单输出）

### 4.2 两个秘密、两条通道

| 秘密 | EVM | TWAK | 实测依据 |
|---|---|---|---|
| 密钥材料 | Keystore-V3 文件 | `~/.twak/wallet.json`（AES-256-GCM 助记词 + PBKDF2，**无机器绑定字段 → 可移植** [实测源码核实]）+ `credentials.json` | twak 自身把该文件当备份单元 |
| 解锁密码 | `WALLET_PASSWORD` env | `TWAK_WALLET_PASSWORD`（twak 解析序：`--password`→env→keychain [实测源码]；官方文档明示 env "Suitable for CI/CD and containerized"） | |

### 4.3 SDK 交付

```python
TWAKProvider(
    chain="bsc",            # 或 "bsctestnet"（实测; "bsc-testnet" 被 CHAIN_UNSUPPORTED 拒绝 → 1a 修 bug）
    twak_bin="twak",        # 可指向 node_modules/.bin/twak 锁版本
    home=None,              # 非 None: 子进程 HOME=<home> → 可重定位 ~/.twak —— 实测有效
                            # （HOME 指空目录, twak 立即报 not configured）。解决: AgentCore 只读
                            # code mount / 多 agent 同机隔离 / 测试隔离。S-5 落地后切 TWAK_HOME, 签名不变
    expected_address=None,  # 首操作前比对 wallet address, 不符抛 WalletIdentityMismatch（INV-4）
    auto_create=True,       # 部署必须 False: 钱包只能来自物化, 缺失即报错, 绝不静默新建
)

def materialize_twak_home(*, wallet_json, credentials_json=None, home) -> Path:
    """SM 取出的密文写回 <home>/.twak/（幂等; 0700/0600）—— studio
    ensure_keystore_materialized 同构, 第二个钱包种类。"""

# bnbagent/core/env.py —— SDK 不在 import 期自动加载（库纪律, F: 评审修正 C-1）
def load_env(root=None) -> list[Path]:
    """先 .env.local 后 .env, 均 override=False
    → 优先级: shell 真实环境 > .env.local > .env（Next.js 同款）。
    顺序必须 local 在前: override=False 下先加载者占位; 反过来(.env 先+override=True)
    会让开发残留的 .env.local 踩掉部署 secret bundle 注入的密码——事故路径。
    root 默认 cwd, 不向上爬目录（SDK 无项目根标记; 锚定由调用方显式传）。返回已加载文件列表。"""
```

### 4.4 部署配方（studio 模式复用）

| bundle 键 | evm-local（现状不动） | twak（新增） |
|---|---|---|
| 密钥材料 | `WALLET_KEYSTORE_JSON` | `TWAK_WALLET_JSON`（+`TWAK_CREDENTIALS_JSON`） |
| 解锁密码 | `WALLET_PASSWORD` | `TWAK_WALLET_PASSWORD` |
| API 凭证 | — | `TWAK_ACCESS_ID` / `TWAK_HMAC_SECRET`（官方明示为 CI 注入设计） |

冷启动：`_load_runtime_secrets()`（现有）→ `materialize_twak_home(home=可写目录)` → `TWAKProvider(home, expected_address, auto_create=False)`。

**AgentCore 形态矩阵**（[代码] 描述符模板自述 + [实测] HOME 重定位）：

| 形态 | `.twak/` 文件 | twak CLI 进程 | 结论 |
|---|---|---|---|
| `direct_code_deploy`（studio 默认） | ✓ 可物化（/tmp 通道 studio 已上线验证） | ✗ 托管 Python 3.12 镜像无 Node | 只能 REST/NaaS（Phase 3+） |
| `container`（描述符一行切换） | ✓ | ✓ 镜像装 Node≥20 + `@trustwallet/cli` | **CLI 形态全链路可行** |

### 4.5 已知边界

- **无 import**（[实测源码] 0 条导入路径，create 只生成新助记词）→ 换 kind 必换地址；studio 架构文档已接受"重新 8004 register"；长期解 = ERC-8004 agentWallet/set-wallet（随 S-6 跟踪）；
- twak `serve --rest` 的 bearer = `TWAK_HMAC_SECRET`（双重身份，必须进 secret manager）；`--auto-lock` 适合长驻服务。

---

## 5. 输出解析硬化【Phase 1a】

全部有实测依据（错误信封三形状互不一致）：

1. **成功判定 = 退出码 + `error` 字段，不信任 `success`**（erc8183 unknown-command 错误无 success 字段；x402 校验错误有 `success:false`；x402 结算错误又没有——[实测] 三个变体）；
2. tx hash 容错链 `hash → txHash → transactionHash`（REQ-3：spec 说 `{success, txHash}`，CLI 实际 `hash`/`approveHash`）；`fund` 带回 `approveHash`；
3. **x402 request 只解析 stdout**（[实测] 横幅走 stderr，stdout 纯 JSON——绝不可合并流解析）；
4. `unknown command/option` → 升级提示；7 个 4-byte 错误选择器映射保留（[gaps]）。

---

## 6. 命名表 + 术语表

| 项 | 定名 | 理由 |
|---|---|---|
| 异常 | `UnsupportedWalletOperation(NotImplementedError)` | 继承保持向后兼容；message 模板 = 能力名+原因+替代路径+REQ/S 编号 |
| 异常 | `WalletIdentityMismatch` | expected_address 比对失败 |
| 标志 | `fund_bundles_approval`（用户确认改名） | 主谓清楚："fund 自带 approval" |
| 类 | `TwakX402Payer`（Twak 驼峰） | 复合名里全大写缩写不可读；`TWAKProvider` 已发布不动，docstring 注明同源 |
| 方法 | `payer.quote()` / `payer.request()` | 与 CLI 动词、x402 语义双对齐；`pay()` 不准（缓存命中可能不付钱） |
| 信封 | `X402PaymentResult` / `X402Quote` | |
| 方法 | `capabilities()`（方法非 property） | 对齐 EIP-5792 查询语义，未来可带参 |
| 术语 | self-broadcasting wallet（自广播钱包） | robot-ux 定义，≈ viem json-rpc account |
| 术语 | 委托型支付（delegated payment） | L402 模式：把 PaymentRequired 整段交给外部支付方 |

---

## 7. 新钱包接入契约（五步）

1. 继承 `WalletProvider`，实现 `address` + 真正会的 `sign_*`（`sign.*` 能力自动点亮；**不支持就不要覆写**）；
2. 声明 `kind` + 执行面能力（`calls.arbitrary` 或 `intents.*`）；
3. 自广播 → 覆写 `make_executor()` 返回自身 + intent 分发表；纯签名 → 什么都不做（默认 LocalExecutor）；委托型支付 → 覆写 `make_x402_payer()`；
4. 注册 `create_wallet_provider` 工厂；
5. 套一致性测试模板（能力 ↔ 行为）。

成本对照：**MPC（签名型）接入 x402 = 0 行代码**（实现 `sign_typed_data` 即自动获得 X402Signer / 未来 LocalX402Payer）；agentic/托管型 = 一个 payer 类（模板 = TwakX402Payer）。

---

## 8. 实施计划

### Phase 1a — 8183 核心接入 + 基线修复（PR #1，±900 行含测试）

| 文件 | 改动 |
|---|---|
| `wallets/twak_provider.py` | chain key bug 修复；分发表（§3.1）；守卫①–⑤；解析硬化（§5）；sign_message 三件适配；**sign_typed_data 删除 spec 调用路径、改为直接抛 `UnsupportedWalletOperation`（P0）** |
| `wallets/errors.py`（新） | `UnsupportedWalletOperation`（**F-1：提前到 1a**，守卫从第一天用对类型） |
| `wallets/intents.py` | ERC8183 常量 ×13（已落盘） |
| `wallets/wallet_provider.py` | `fund_bundles_approval` ClassVar（改名） |
| `core/contract_mixin.py` | `_execute_intent()`（已落盘） |
| `erc8183/commerce.py` | 8 写方法 → Intent（5 已落盘补 3；create_job jobId 双源） |
| `erc8183/router.py` / `policy.py` | 5 写方法 → Intent |
| `erc8183/client.py` | fund 跳过 approve 逻辑 |
| 测试 | 12 intent 参数映射；fund 预检两路；守卫全路径；chain key；hash 三变体；错误信封 fixture；EVM 回归（现有断言不变） |
| 文档 | 本设计文档落盘；gaps 补 S-5/S-6/S-7 |

### Phase 1b — 能力模型（PR #2，±400 行）

`capabilities.py`（9 常量）；sign_* 降级 + 自动推导 + `supports()` + describe 扩展 + make_executor 入口检查；EVM/TWAK 能力声明；MPC stub 删除 + **TWAK 的 `sign_typed_data` override 删除（P0，与 MPC stub 同批落回基类默认闸——能力自动推导随之对 TWAK 天然正确，零手工例外）**；`protocols.py` 窄协议 + X402Signer/negotiation 类型标注切换；**X402Signer 构造期能力闸（getattr 兜底）**；`tests/test_wallet_conformance.py` 模板。

### Phase 1c — Custody + x402（PR #3，±600 行）

TWAKProvider `home`/`expected_address`/`auto_create` + `x402_quote`/`x402_request` 管道（**F-3：quote 不触发 ensure_wallet**）；`materialize_twak_home`；`x402/payer.py`（Protocol + 两 dataclass）；`x402/twak.py`（五项预检，**F-2：窗口默认 3600s**；按报价记账）；`make_x402_payer`（默认能力闸 + twak 覆写 + kwargs 透传）；`core/env.py` `load_env()`；实测捕获全部入 `tests/fixtures/twak_x402/`；`wallets/README.md` twak 说明（角色矩阵/custody 配方/AgentCore 矩阵/SIWE 前提/三层守卫图）。

### Phase 2/3+（上游触发）

| 触发 | 动作 | 规模 |
|---|---|---|
| REQ-1 上线 | submit `--opt-params` + 解除守卫② — **已执行 2026-06-12**（twak v0.19.0；job 150 链上证明：JobInitialised 携带完整 deliverable_url optParams）。submit 本身确如预测 <10 行；实际 diff 同批还退役了守卫①（S-1 同版上线，opt_params 改为全写命令透传）与 fund 的 S-2 预检 | <10 行（submit 本体；含守卫退役略超） |
| sign-typed-data 上线（**团队判断：大概率不会发生，P0**） | 按当时 CLI **实测**重新实现 override（历史代码在 git；eip712-twak-spec 仅作参考）；验证 X402Signer 对 twak 点亮 | 小 |
| 买方需求 / 与 studio 协调达成 | LocalX402Payer 自 studio buyer.py 上提；make_x402_payer 默认实现升级（能力增强，非破坏） | 中 |
| REQ-2 上线 | paymaster 透传 + 撤 WARNING（部分解除：bsc 主网 twak 自动赞助 MegaFuel，自 v0.18.0；bsctestnet 仍自费、上游在做） | 小 |
| S-2 上线 | 删 fund 预检 — **已执行 2026-06-12**（改传 `--expected-budget`，合约侧原子 `BudgetMismatch()`，twak v0.19.0） | 小 |
| 第二个 transport（REST/NaaS） | 从 `_run` 提取 transport 接口 | 机械重构 |

### studio 移交清单（不在本仓库）

trust kind 接线（`ensure_twak_materialized` + bundle 4 键）；AgentCore `container` 形态决策（Node≥20）；container 内 HOME 重定位冒烟；SIWE 绑定流程文档；政策层（host/日/月）确认零改动复用。

### Backlog（已记录，不阻塞）

- bsctestnet 真实冒烟（13 个 intent 全生命周期）——触发：首次联调或 Phase 2 前（用户决定本期不含）
- message-signing 钱包级策略（防 SIWE 钓鱼）——若需要，加在 WalletProvider 基类层对所有钱包统一
- 装配期过滤落点（apex 工具组装层）确认
- 向 twak 团队问询：NaaS 端 server-side spending policy 是否有/计划有

---

## 9. 上游需求（提交至 gaps 文档）

| ID | 请求 | 我们的临时方案 |
|---|---|---|
| S-5 | `TWAK_HOME`（或等价）配置目录覆盖 | 子进程 `HOME` 覆盖（实测有效但属未承诺行为） |
| S-6 | `wallet import`（助记词/私钥导入） | 接受换地址重注册；跟踪 ERC-8004 set-wallet |
| S-7 | `x402 request --json` 输出包含支付回执元数据（`{payment:{amount,asset,txHash}, body}`——`PAYMENT-RESPONSE` 头里本来就有，twak 消费了未透出） | 按报价金额记账 |
| 问询 | NaaS server-side spending policy | studio 政策层 + `--max-payment` |

（原 S-7"自定义 header"已撤回——实测 pieverse 402 challenge 无需 Bearer，此前 400 系 body 形状错误。）

---

## 10. 证据库

### 10.1 决策-依据审计表（28 项）

| # | 决策 | 依据 | 等级 |
|---|---|---|---|
| 1 | chain key = `bsctestnet` | `bsc-testnet` 被 `CHAIN_UNSUPPORTED: Did you mean "bsc"?` 拒绝 | 实测 |
| 2 | 8183 全量 Intent 化 | erc8004 模板 + intents.py docstring "(… fund a job, …)" 自证规划 | 代码 |
| 3 | 分发表 13 个 intent | gaps 全 ✅（job 137/138 等）+ 0.18 help | 实测+gaps |
| 4 | fund 预检 | S-2 + status JSON budget 扁平字符串 | 实测 |
| 5 | submit fail-fast | gaps Note 1：tx `0xfa057a11…81f4` 链上实证 optParams 空 | gaps |
| 6 | paymaster WARNING | REQ-2 + "必须预存 BNB" 实测 | gaps |
| 7 | 解析硬化 | REQ-3 + 错误信封三形状实测互异 | 实测 |
| 8 | sign_message 三件适配 | S-4 + ecrecover 闭环论证 | gaps+设计 |
| 9 | sign_typed_data **不携带任何实现**（直接抛错；**P0 修订**，推翻原"保留 spec 路径"） | 团队判断 twak 大概率不支持 + P4 自洽（不再按 spec 超前实现）+ 修复能力自动推导矛盾（详见 §11 P0） | 设计 |
| 10 | register 原子 --metadata | gaps agent 1350/1351 | gaps |
| 11 | fund_bundles_approval 属性 | 粒度纪律 + 用户确认 | 设计 |
| 12 | capabilities 开放集+自动推导 | EIP-5792 / goat / thirdweb | peer |
| 13 | sign_* 默认抛错 | thirdweb 可选方法 / agentkit 描述异常 / goat 无签名契约 | peer |
| 14 | MPC stub 删除 | "覆写即能力"反例纪律（C-4） | 设计 |
| 15 | make_executor 入口检查 | 错误提前到构造点（C-3） | 设计 |
| 16 | 窄协议 | coinbase ClientEvmSigner 同形 | peer |
| 17 | X402Signer 构造闸 | (C) 四道闸推演，组合期空缺 | 设计 |
| 18 | 静态类型边界→运行期承重 | 结构化 Protocol 语义 + studio 兼容 | 代码 |
| 19 | make_x402_payer 1c 立 | 默认抛错=1b 标准模式；create_wallet_provider kwargs 先例；agentic 钱包时序风险 | 设计 |
| 20 | make_signer 不立 | 恒等工厂对比表；触发条件文档化 | 设计 |
| 21 | X402Payer Protocol | studio fetch_with_payment + twak request 双实现蒸馏 | 代码+实测 |
| 22 | TwakX402Payer 五项预检+按报价记账 | quote 双链实测 + 成功输出=body 透传实测 | 实测 |
| 23 | SigningPolicy 语义等价不移植 | 检查材料不过境 + asset≡domain.verifyingContract | 设计 |
| 24 | studio 政策层零改动复用 | policy.py/buyer.py 实读：作用在 URL+USD、调 payer 前 | 代码 |
| 25 | home/materialize/expected_address/auto_create | HOME 覆盖实测 + wallet.json 可移植源码核实 + studio 身份锚定教训 | 实测 |
| 26 | AgentCore container 前提 | 描述符模板自述 + Python 3.12 镜像无 Node | 代码 |
| 27 | load_env 语义 | dotenv override 语义推演（shell 必须赢） | 设计 |
| 28 | S-5/6/7 + 问询 | 各自实测出处（§10.2） | 实测 |

### 10.2 实测记录（本设计过程，全部可复现）

| 实验 | 结果 |
|---|---|
| `twak --version` / `node_modules` | 0.18.0（本机与仓库一致；`twak_bin` 可锁版本） |
| `twak chains --json` | BSC key=`bsc`；列表无 testnet 键 |
| `erc8183 status 137 --chain bsctestnet` | ✅ 返回 job（Submitted）；`bsc-testnet` → CHAIN_UNSUPPORTED |
| `wallet sign-typed-data` | `{"error":"error: unknown command 'sign-typed-data'","errorCode":"VALIDATION_ERROR"}` exit 1 |
| `HOME=/tmp/probe twak wallet status` | `"agentWallet": "not configured"`（真实 home 下 configured）→ HOME 重定位有效 |
| `x402 quote http://…` / `https://127.0.0.1…` | HTTPS-only / loopback 拒绝（VALIDATION_ERROR，带 success:false） |
| `x402 quote`（x402.org, base-sepolia） | `accepts:[]` + "none on chains this client supports" → quote 过滤不可付路由 |
| `x402 quote`（onesource, Base 主网） | 完整 accepts 条目（payTo/asset/amount/transferMethod/maxTimeoutSeconds=**3600**/…） |
| `x402 quote`（pieverse, BSC+U, 正确 body） | `eip155:56` / U / 0.1U / payTo `0x4ba0…6751` / maxTimeout=300 ——**402 challenge 无需 Bearer**（原 S-7 撤回依据） |
| `x402 request`（无资金） | 横幅走 stderr、stdout 纯 JSON；结算拒绝信封 `{error,errorCode}` 无 success |
| `x402 request`（0.1 U 实付，pieverse） | 成功输出 = 端点 body 透传 `{tx_hash, status:"submitted", payer, note:"…must be SIWE-bound…"}`；链上确认 `0x09b1af61…22f8` = `transferWithAuthorization(from 0x163b…, to 0x4ba0…, 0.1e18)` EIP-3009 gasless |
| 服务端业务错误 | `amountUsd must be at least 0.1`（`X402UnsupportedPaymentError` `_tag` 透传） |
| twak bundle grep 限额键 | 仅 `maxPayment*`——无 daily/monthly/count/host |
| keychain 非交互 | request 全程无提示（keychain 取密码）→ headless 链路可用 |

### 10.3 同行调研结论（两份报告要点）

- **x402 生态**（x402-foundation/coinbase）：拦截器(402循环)/x402Client核心(注册表+策略+钩子)/极小签名协议三层；守卫是选择层可组合纯函数；换后端=适配签名协议；`on_payment_required` 钩子可整段绕过内置支付（官方逃生门）。**无公开实现把整个 402 流程交给外部 CLI**——最近先例是 L402/aperture（lnd 进程外付款，maxCost 守卫）与 a2a-x402 executor 委托。
- **钱包抽象**（agentkit/goat/thirdweb/viem/EIP-5792）：三家独立收敛于"高级操作必选、裸签可选"；goat 装配期过滤（LLM 看不到必败工具）；EIP-5792 = 能力自省标准名；推荐组合 = 能力集路由 + 分层接口 + 描述性异常兜底（本设计照此落地，分层接口以窄协议形式实现）。

### 10.4 端到端流程走查（final review）

① twak 买方 8183 全程（create→budget→register→fund→settle）无断点；卖方 submit 被守卫②明确拦截（REQ-1 前预期行为；2026-06-12 起守卫②退役、submit 透传 `--opt-params`——见 §8/§11）。
② twak x402：studio 政策 → make_x402_payer → quote（不建钱包）→ 五项预检 → request → 按报价记账 → 统一信封。三层纵深成立。
③ 部署冷启动：SM → env → materialize → TWAKProvider(home, expected_address, auto_create=False) → 身份核对失败即停。前提：container 形态。

---

## 11. 评审修正记录（决策演化，供追溯）

| 编号 | 修正 | 原因 |
|---|---|---|
| C-1 | SDK 不在 import 期 load_dotenv → 显式 `load_env()` | 库副作用反模式；studio(bag) 作为应用已自行加载 |
| C-2→反转 | make_x402_payer 原延至 Phase 3，后**反转进 1c** | 1b 后"默认抛错"成为标准模式（非死代码）；kwargs 透传消除签名冻结；agentic 钱包先于 LocalX402Payer 到来的时序风险 |
| C-3 | make_executor 入口能力检查 | sign_* 降级后错误会推迟到 LocalExecutor 深处 |
| C-4 | "不支持就不要覆写"纪律 + MPC stub 删除 | 能力自动推导的反例（覆写抛错会被误判有能力） |
| 撤回 | 原 S-7（x402 自定义 header） | 实测 pieverse 402 challenge 无需 Bearer（此前 400 系 body 错误）；归账由 SIWE 预绑定解决 |
| F-1 | UnsupportedWalletOperation 提前到 1a | 守卫报错避免改两遍 |
| F-2 | 窗口预检默认 600s → **3600s** | 实测 Bazaar 规范端点 3600s；twak 路径实际签名窗口由 twak 决定，检查对象语义不同 |
| F-3 | x402_quote 不触发 _ensure_wallet | quote 只读无需钱包；否则报价即可能静默建钱包（违反 INV-4） |
| **P0**（用户 review） | **TWAK 不携带 sign_typed_data 实现**：1a 删 spec 调用路径改直接抛错，1b 删 override 落回基类默认闸 | ① 团队判断 twak 大概率不会支持；② P4 自洽——批评 bsc-testnet 按 spec 超前实现翻车，却保留同模式的 spec 调用路径，双标；③ **修复能力自动推导矛盾**："覆写即能力"规则下保留 override 会让 TWAK 被误判具备 sign.typed_data，击穿闸0/闸1 |
| F-4（2026-06-12 联调实发现） | twak 以 JSON **字符串**返回数字 id（`"150"`，实测）→ `_create_job`/`_register` 将 jobId/agentId 归一为 `int` 再入信封 | 信封修正：本地 executor 从事件日志取到的是 int，两个执行后端必须遵守同一信封，str 化的 jobId 会让下游 web3 uint256 调用爆炸 |

## 12. 残留风险/假设（带触发器，均不阻塞）

| 风险 | 等级 | 处置 |
|---|---|---|
| HOME 重定位在 Linux 容器同效（macOS 已实测；Node `os.homedir()` POSIX 读 `$HOME` 同构） | 低 | studio 联调冒烟 |
| twak fund 在 allowance 已足时是否重复 approve | 极低 | bsctestnet 冒烟时确认（gas 浪费级，非正确性） |
| `--metadata` 值含 `=` 的解析边界 | 低 | 1a 测试期实测 |
| request 对非 JSON 响应体的渲染 | 低 | 1c 实现期实测（X402PaymentResult.response 按 raw 设计兜底） |
| REQ-1 时间表 | ~~低~~ 已消除 | **已解除**：v0.19.0 上线（2026-06-12 执行，job 150 链上证明） |
| quote→request TOCTOU | 低 | --max-payment + prefer-* + 文档 |
