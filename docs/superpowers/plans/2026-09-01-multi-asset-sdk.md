# USDT/USDC 多资产 SDK 实施计划

**日期：** 2026-09-01  
**分支：** `feat-usdt-usdc-multi-asset-payments`  
**范围：** Python 与 TypeScript SDK；BSC Mainnet/Testnet；ERC-8183 与 B402 资产能力  
**设计来源：** `bnbchain-studio/docs/superpowers/specs/2026-08-31-usdt-usdc-multi-asset-payments-design.md`

## 约束

- Python 与 TypeScript 的公开行为、错误边界和 catalog 快照保持一致。
- 内部只使用 canonical AssetId；`USDC`/`USDT` 仅在 chain 已知时解析。
- Mainnet 使用 `BINANCE_PEG_USDC` / `BINANCE_PEG_USDT`，Testnet 使用 `TEST_USDC` / `TEST_USDT`。
- 旧 `paymentToken`、默认 token metadata/balance/allowance/approve、`createJob` API 保持兼容。
- `fund` 以链上 `jobPaymentToken` 为权威，并仅授权 job-bound token；不自动切换 token。
- 首期不为 evm-local 或 Turnkey 开放 B402 Permit2；USDC/USDT 的 Permit2-only route 由上层返回 typed unsupported。
- 不发送链上交易，不修改已发布 Commerce/Router/Policy 地址。
- Testnet U 使用当前 SDK/Commerce 的默认 token 身份；发布前仍需按设计完成链上来源和 B402 能力核验。

## Task 1：双语言资产目录

新增 Python/TypeScript 等价的资产模型和 `(chainId, AssetId)` catalog：canonical ID、UI symbol、checksum 地址、decimals、B402 methods、可选 EIP-3009 domain、默认资产标记。

测试先覆盖：

- 两个网络的 snapshot；
- network-contextual alias 解析；
- canonical ID、地址反查和大小写无关地址输入；
- 未知 chain、跨网络 ID、symbol 无网络上下文、重复地址 fail closed；
- atomic amount 使用 `int`/`bigint`，不引入浮点换算；
- 旧 `known_payment_tokens` / `knownPaymentTokens` 只包含真正允许 EIP-3009 签名的 token。

## Task 2：ABI 与 Python ERC-8183 客户端

同步新 Commerce ABI，扩展 Python low-level 和 facade：

- `create_job_with_token`；
- `job_payment_token`；
- `is_payment_token_supported`；
- AssetId/address metadata、balance、allowance、approve helper；
- address-keyed ERC-20 client/metadata cache；
- `fund` 在 approve 前读取 job token，验证可选 expected token，并对 job-bound token 做精确预算授权。

保持旧 helper 指向 Commerce 默认 token。测试覆盖新 selector/intent、事件 jobId 恢复、6/18 decimals、cache、默认兼容、job-token mismatch、余额/allowance 和 self-broadcasting wallet 路径。

## Task 3：TypeScript ERC-8183 对齐

实现 Task 2 的 TypeScript 等价 API、类型和测试；同步公开 exports。Python/TypeScript fixture 使用同一地址和预期语义，防止命名或默认行为漂移。

## Task 4：协商与 funded-job 校验

双语言复用 `TermSpecification.currency`：

- Buyer 显式 token 时 request/response 必须一致；省略时只使用 Commerce 默认 token；
- Seller 只接受 catalog、项目允许且 Commerce enabled 的 token；
- response 原样返回 currency，并按对应 decimals 使用 atomic price；
- `UNSUPPORTED` detail 只列 canonical AssetId；
- quote verifier 校验 chain、Commerce、currency；
- funded-job verifier 校验 signed quote currency、链上 job token、catalog token 三者一致。

旧无 currency request 只回退默认 token；默认 token 不可用时返回 unsupported，不选择其他 token。

## Task 5：钱包 intent 与权限边界

- 新增 `erc8183.create_job_with_token` semantic intent。
- evm-local/Turnkey 继续走受 chain/target 约束的通用交易执行。
- TWAK 传递 token/asset；旧 CLI 不支持时返回 typed version/capability error。
- Altana permission 增加精确 selector，并将 ERC-20 spend cap 绑定到 catalog token，不开放任意 target-wide call。
- 更新 paymaster selector fixtures；保留 sponsorship 失败后既有 self-pay 语义。
- SigningPolicy 只为支持且已验证 EIP-3009 domain 的 U token 建 allowlist，不把 USDC/USDT 伪装成 EIP-3009 token，也不开放 Permit2 raw typed-data。

## Task 6：B402 资产辅助能力与文档

在不实现 evm-local/Turnkey Permit2 的前提下，向 B402 payer/helper 暴露 catalog 驱动的 expected asset metadata 与 route capability 判断；TWAK/Altana 继续使用既有 delegated payer 安全边界。增加 typed unsupported-wallet-route 错误，确保不会跨资产 fallback。

更新中英文现有说明中的默认 token/唯一 token 假设、钱包矩阵、API 示例和 Testnet U 发布前核验说明；不声称未完成 live test 的 route 已可用。

## 验证与提交

每个 Task 遵循 RED → GREEN → review → commit：

- Python：目标 pytest，随后全量 pytest、ruff check/format check；
- TypeScript：目标 Vitest，随后 `pnpm test`、`pnpm typecheck`、`pnpm lint`、`pnpm build`；
- ABI codegen/公开 API snapshot；
- `git diff --check` 与工作区污染检查；
- 最终做双语言 parity review 和完整分支 review。

真实网络探测、Testnet U 最终确认、TWAK live CLI 和链上 E2E 属于发布验证，不在本地单测中伪造“已通过”。
