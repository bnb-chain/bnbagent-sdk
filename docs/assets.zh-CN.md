# BNB Chain 支付资产目录

SDK 在 BSC Mainnet（`eip155:56`）和 Testnet（`eip155:97`）使用统一的规范资产
标识。业务配置和结构化输出应保存 `AssetId`，不要把 UI symbol 当作资产身份。

| 网络 | AssetId | UI | Decimals | B402 kind（method / name / version） |
| --- | --- | --- | ---: | --- |
| Mainnet | `U` | U | 18 | `eip3009 / United Stables / 1`；`permit2-exact / United Stables / 1` |
| Mainnet | `USD1` | USD1 | 18 | `eip3009 / World Liberty Financial USD / 1` |
| Mainnet | `BINANCE_PEG_USDC` | USDC | 18 | `permit2-exact / USD Coin / 1` |
| Mainnet | `BINANCE_PEG_USDT` | USDT | 18 | `permit2-exact / Tether USD / 1` |
| Testnet | `TEST_U` | U | 18 | `eip3009 / United Stables / 1` |
| Testnet | `TEST_USD1` | USD1 | 18（占位） | 无（不可用） |
| Testnet | `TEST_USDC` | USDC | 6 | `permit2-exact / USD Coin / 1` |
| Testnet | `TEST_USDT` | USDT | 18 | `permit2-exact / USDT Token / 1` |

Mainnet `USD1` 的精确地址是
`0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d`，为 18 decimals 的 EIP-3009
资产，EIP-712 domain 是 `World Liberty Financial USD / 1`。它不支持 Permit2，
因此 `permit2-exact` 和 `permit2-upto` 都不是 USD1 的可用 route。

Testnet `TEST_USD1` 仅是地址待确认的
`0x0000000000000000000000000000000000000000` placeholder，不是 active 资产：不得
出现在默认 Seller、支付 route、签名、余额/allowance 查询、Commerce allowlist 或审计
actual facts 中。对 Testnet `USD1` 的支付型解析必须返回 typed unavailable；未来启用前
须核验真实地址、decimals、EIP-3009 domain、B402 能力和 Commerce allowlist，并完成 live
验证。

`U`、`USD1`、`USDC`、`USDT` 只是带网络上下文的友好别名。例如，Mainnet 的 `USD1`
解析为 `USD1`，Mainnet 的 `USDC`
解析为 `BINANCE_PEG_USDC`，Testnet 的 `USDC` 解析为 `TEST_USDC`。没有网络上下文
时，SDK 只接受规范 AssetId，并对未知、跨链或重复条目关闭失败。

`PaymentAsset.b402_kinds` / `PaymentAsset.b402Kinds` 是逐 transfer method 的精确
scheme identity，不能从 UI symbol 推断。每个 `b402_methods` / `b402Methods` 条目必须且
只能有一个 `{method, name, version}`；缺失、重复、额外 method 都会让 catalog 构造失败。
`eip3009` 的 name/version 还必须与 `eip3009_domain` / `eip3009Domain` 完全一致。

TypeScript 为兼容旧版调用方手写的 `PaymentAsset` object literal，将其中的 `b402Kinds`
保留为可选输入字段；这不表示 catalog 接受缺失 identity。`AssetCatalog` 构造器仍会严格拒绝
缺失值，而 `getAsset`、`getAssetByAddress`、`listAssets` 返回更强的
`CatalogPaymentAsset`，其 `b402Kinds` 在类型和运行时都保证存在且完整。

Testnet 当前 Commerce 默认 token `0xc70B...5565` 暂以 `TEST_U` 表示，以兼容既有
SDK 的已验证 EIP-3009 domain。其正式来源和完整 B402 能力仍属于发布前核验项；目录
没有写入尚未确认的 Testnet U 地址，也没有宣称该 token 已验证 Permit2。

USD1 只记录 EIP-3009；USDC/USDT 只记录 `permit2-exact`，三者都不得被伪装成其他
method。首期 `evm-local` 和 Turnkey 不开放 Permit2 签名；上层遇到 Permit2-only 路由时应
返回类型化 unsupported 错误，不能通过 raw typed-data 或自动切换资产绕过。Buyer 省略资产
仍默认 U；余额不足仅可提示可用备选，绝不自动切币。

金额边界接受普通非负十进制字符串，并返回 Python `int` 或 TypeScript `bigint` 的
atomic amount。指数、正负号、空字符串、超出 token decimals 的小数和 JavaScript
`number` 均被拒绝，避免二进制浮点误差。
