# B402 多资产与钱包路由

B402 调用方必须先固定一个 expected asset，再检查钱包能否执行这个资产的指定
transfer method。SDK 不在 capability helper 内选择默认币，也不会在 route 不可用时切换到
另一个资产。

## Expected asset

Python：

```python
from bnbagent.networks import AssetId
from bnbagent.x402 import resolve_b402_asset

expected = resolve_b402_asset("eip155:97", AssetId.TEST_USDC)
assert expected.symbol == "USDC"
assert expected.decimals == 6
assert expected.b402_kinds[0].name == "USD Coin"
```

TypeScript：

```ts
import { AssetId } from "@bnbagent/sdk/networks";
import { resolveB402Asset } from "@bnbagent/sdk/x402";

const expected = resolveB402Asset("eip155:97", AssetId.TEST_USDC);
const kind = expected.b402Kinds[0]; // permit2-exact / USD Coin / 1
```

输入网络只能是 BSC Mainnet/Testnet 的 chain id（`56`/`97`）或规范 CAIP-2
（`eip155:56`/`eip155:97`）。资产只能是该网络的 canonical `AssetId` 或已 checksum
的 catalog 地址。`USDC`、`USDT` 这类裸 UI symbol、跨链 AssetId/地址、未知网络和畸形地址
都会关闭失败。quote 中已有 `network + asset` 时，可用
`expected_asset_from_payment_option` / `expectedAssetFromPaymentOption` 解析同一个资产。

金额仍保留为 atomic units：Python `int`、TypeScript `bigint`。helper 不做浮点或 USD
换算。

服务端 `/supported` 的 B402 Scheme 必须用 catalog 中逐 method 的 `method + name + version`
精确匹配；symbol 只用于 UI 展示，不能代替链上 token name，也不能用于推断 Permit2 identity。
例如 Mainnet USDT 是 `Tether USD / 1`，Testnet USDT 是 `USDT Token / 1`。

## 首期钱包矩阵

| 钱包路由 | `eip3009` | `permit2-exact` | `permit2-upto` |
| --- | --- | --- | --- |
| `evm-local` | 仅 active catalog 中有已验证 EIP-3009 domain 的资产（如 U、USD1） | 不支持 | 不支持 |
| `turnkey` | 仅 active catalog 中有已验证 EIP-3009 domain 的资产（如 U、USD1） | 不支持 | 不支持 |
| `twak` | catalog 声明时可交给 delegated payer | catalog 声明时可交给 delegated payer | 不支持 |
| `altana` | catalog 声明时可交给 delegated payer | catalog 声明时可交给 delegated payer | 不支持 |

调用 `require_b402_wallet_route` / `requireB402WalletRoute` 可验证指定 route。失败时抛出
`UnsupportedWalletRouteError`，稳定携带 wallet kind、network/chain、canonical AssetId 和
transfer method，便于 Studio 提示用户，但错误本身不返回替代币种。

```python
from bnbagent.x402 import require_b402_wallet_route

route = require_b402_wallet_route("altana", expected, "permit2-exact")
assert route.expected_asset is expected
assert route.delegated is True
```

U 作为默认资产是上层 Buyer 策略，不是 helper 的自动选择行为。余额不足或 route 不支持时，
上层可以展示其他可选币，但必须由用户明确选择后重新固定 expected asset。

## 安全与发布边界

- USDC/USDT 目前只登记 `permit2-exact`，绝不能伪装为 EIP-3009 token。
- `evm-local`/Turnkey 首期没有 Permit2 signing/approval 实现；helper 会返回类型化 unsupported。
- TWAK/Altana 的 delegated 能力表示支付由其既有受约束 payer 执行，不等于向应用开放 raw
  Permit2 typed-data 或任意 token/target。
- capability helper 只描述本地 catalog 与钱包策略，不证明 live route、余额、allowance、服务端
  challenge 或结算可用。
- Testnet U 当前沿用 SDK/Commerce 的 `TEST_U` 身份与既有已验证 EIP-3009 domain；正式发布前
  仍需核验 token 来源和 B402 live 能力，不能把本地单测当成链上验证。
