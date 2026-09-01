# BNB Chain 支付资产目录

SDK 在 BSC Mainnet（`eip155:56`）和 Testnet（`eip155:97`）使用统一的规范资产
标识。业务配置和结构化输出应保存 `AssetId`，不要把 UI symbol 当作资产身份。

| 网络 | AssetId | UI | Decimals | B402 能力 |
| --- | --- | --- | ---: | --- |
| Mainnet | `U` | U | 18 | `eip3009`、`permit2-exact` |
| Mainnet | `BINANCE_PEG_USDC` | USDC | 18 | `permit2-exact` |
| Mainnet | `BINANCE_PEG_USDT` | USDT | 18 | `permit2-exact` |
| Testnet | `TEST_U` | U | 18 | `eip3009` |
| Testnet | `TEST_USDC` | USDC | 6 | `permit2-exact` |
| Testnet | `TEST_USDT` | USDT | 18 | `permit2-exact` |

`U`、`USDC`、`USDT` 只是带网络上下文的友好别名。例如，Mainnet 的 `USDC`
解析为 `BINANCE_PEG_USDC`，Testnet 的 `USDC` 解析为 `TEST_USDC`。没有网络上下文
时，SDK 只接受规范 AssetId，并对未知、跨链或重复条目关闭失败。

Testnet 当前 Commerce 默认 token `0xc70B...5565` 暂以 `TEST_U` 表示，以兼容既有
SDK 的已验证 EIP-3009 domain。其正式来源和完整 B402 能力仍属于发布前核验项；目录
没有写入尚未确认的 Testnet U 地址，也没有宣称该 token 已验证 Permit2。

USDC/USDT 只记录 `permit2-exact` 能力，不进入 EIP-3009 raw signing allowlist。首期
`evm-local` 和 Turnkey 不开放 Permit2 签名；上层遇到 Permit2-only 路由时应返回类型化
unsupported 错误，不能通过 raw typed-data 或自动切换资产绕过。

金额边界接受普通非负十进制字符串，并返回 Python `int` 或 TypeScript `bigint` 的
atomic amount。指数、正负号、空字符串、超出 token decimals 的小数和 JavaScript
`number` 均被拒绝，避免二进制浮点误差。
