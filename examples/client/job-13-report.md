# Job 13 链上时间线报告

> 生成时间：2026-05-08 11:49:44 UTC
> 网络：BSC Testnet（chain_id=97）
> 浏览器：https://testnet.bscscan.com

---

## 涉及地址

| 角色 | 地址 |
|------|------|
| 客户端（client） | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` |
| 提供方（provider） | `0xD8c45dA4e4036f4946132B18fc7568096CB7535f` |
| Commerce 合约 | `0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE` |
| Router 合约 | `0xD7d36D66d2F1B608A0F943f722D27e3744f66F25` |
| Policy 合约 | `0x4F4678D4439feC812Ac7674Bb3Efb4C8f5Fb78A6` |
| 支付代币（U，18 位精度） | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |

---

## Job 当前状态

| 字段 | 值 |
|------|----|
| jobId | 13 |
| 当前状态 | `REJECTED` |
| description（任务描述） | `Latest BNB Chain ecosystem news` |
| 预算 | `1000000000000000000` raw = 1.0000 U |
| expiredAt | `1778326421` = 2026-05-09 11:33:41 UTC（距今还有 1423 分钟）|
| rejectVotes | `2 / 2` |
| verdict | `REJECT` |
| disputed | `True` |
| deliverable hash（链上） | `0xe0b01f59e2f972a468df1949c825ac8159e5a725a9074db0b920485eedea11f8` |

---

## 时间线

### 🆕 `JobCreated` — Commerce — 区块 106214238 — 2026-05-08 11:23:43 UTC

- **Tx**：[`0xab794df936ba4c7bf88bb7130c21be3de7fcc64036c46c0a98642e1df82d64fb`](https://testnet.bscscan.com/tx/0xab794df936ba4c7bf88bb7130c21be3de7fcc64036c46c0a98642e1df82d64fb)
- **发起方**：`0xe376F3E7Fb15B526152F0db6805F1002564cbC2B`

| 参数 | 值 |
|------|----|
| `client` | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` |
| `provider` | `0xD8c45dA4e4036f4946132B18fc7568096CB7535f` |
| `evaluator` | `0xD7d36D66d2F1B608A0F943f722D27e3744f66F25` |
| `expiredAt` | `1778326421` |
| `hook` | `0xD7d36D66d2F1B608A0F943f722D27e3744f66F25` |

### 📋 `JobRegistered` — Router — 区块 106214242 — 2026-05-08 11:23:44 UTC

- **Tx**：[`0xb5b0c52421a2ecc40faf5017c1bb7927b376f71eda64b86145fe498ac9704a58`](https://testnet.bscscan.com/tx/0xb5b0c52421a2ecc40faf5017c1bb7927b376f71eda64b86145fe498ac9704a58)
- **发起方**：`0xe376F3E7Fb15B526152F0db6805F1002564cbC2B`

| 参数 | 值 |
|------|----|
| `policy` | `0x4F4678D4439feC812Ac7674Bb3Efb4C8f5Fb78A6` |
| `client` | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` |

### 💰 `BudgetSet` — Commerce — 区块 106214247 — 2026-05-08 11:23:47 UTC

- **Tx**：[`0x6d2e0aced8adf3287a182144365d776c00dbc2e8b8d31cac47e717d9f7917577`](https://testnet.bscscan.com/tx/0x6d2e0aced8adf3287a182144365d776c00dbc2e8b8d31cac47e717d9f7917577)
- **发起方**：`0xe376F3E7Fb15B526152F0db6805F1002564cbC2B`

| 参数 | 值 |
|------|----|
| `amount` | `1000000000000000000` raw (1.0000 U) |

### 💵 `JobFunded` — Commerce — 区块 106214253 — 2026-05-08 11:23:49 UTC

- **Tx**：[`0xd3181c7ea8b11031cc54ee1ac3305a6124db50af224109d99338122fd93ccdbe`](https://testnet.bscscan.com/tx/0xd3181c7ea8b11031cc54ee1ac3305a6124db50af224109d99338122fd93ccdbe)
- **发起方**：`0xe376F3E7Fb15B526152F0db6805F1002564cbC2B`

| 参数 | 值 |
|------|----|
| `client` | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` |
| `provider` | `0xD8c45dA4e4036f4946132B18fc7568096CB7535f` |
| `amount` | `1000000000000000000` raw (1.0000 U) |

### 🗂 `JobInitialised` — Policy — 区块 106214333 — 2026-05-08 11:24:25 UTC

- **Tx**：[`0xae7354cc62a1149ca6171371b104478e2e1bea2e3fa64a48e28910129d7eeea8`](https://testnet.bscscan.com/tx/0xae7354cc62a1149ca6171371b104478e2e1bea2e3fa64a48e28910129d7eeea8)
- **发起方**：`0xD8c45dA4e4036f4946132B18fc7568096CB7535f`

| 参数 | 值 |
|------|----|
| `deliverable` | `0xe0b01f59e2f972a468df1949c825ac8159e5a725a9074db0b920485eedea11f8` |
| `submittedAt` | `1778239465` |
| `optParams` | `{"deliverable_url": "ipfs://QmW7FviqscbkCyQSUvzL6CCyrXd1RgDehwxanXXf3vGqQc"}` |

### 📤 `JobSubmitted` — Commerce — 区块 106214333 — 2026-05-08 11:24:25 UTC

- **Tx**：[`0xae7354cc62a1149ca6171371b104478e2e1bea2e3fa64a48e28910129d7eeea8`](https://testnet.bscscan.com/tx/0xae7354cc62a1149ca6171371b104478e2e1bea2e3fa64a48e28910129d7eeea8)
- **发起方**：`0xD8c45dA4e4036f4946132B18fc7568096CB7535f`

| 参数 | 值 |
|------|----|
| `provider` | `0xD8c45dA4e4036f4946132B18fc7568096CB7535f` |
| `deliverable` | `0xe0b01f59e2f972a468df1949c825ac8159e5a725a9074db0b920485eedea11f8` |

### ⚖️ `Disputed` — Policy — 区块 106214346 — 2026-05-08 11:24:31 UTC

- **Tx**：[`0x067ee42252ce9b3f21f33515634fec695dd181a4ee893ae4c790b472ddbbaa21`](https://testnet.bscscan.com/tx/0x067ee42252ce9b3f21f33515634fec695dd181a4ee893ae4c790b472ddbbaa21)
- **发起方**：`0xe376F3E7Fb15B526152F0db6805F1002564cbC2B`

| 参数 | 值 |
|------|----|
| `client` | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` |

### 🗳 `VoteCast` — Policy — 区块 106217099 — 2026-05-08 11:45:10 UTC

- **Tx**：[`0xc6a6b7f5349f104a9043e55baf7cee2e7f95e0a553ca81f00e793499bd166e8e`](https://testnet.bscscan.com/tx/0xc6a6b7f5349f104a9043e55baf7cee2e7f95e0a553ca81f00e793499bd166e8e)
- **发起方**：`0x32725EDa7418ce4440ccA6107FEADBf047954A20`

| 参数 | 值 |
|------|----|
| `voter` | `0x32725EDa7418ce4440ccA6107FEADBf047954A20` |
| `rejectVotes` | `1` |

### ✅ `QuorumReached` — Policy — 区块 106217140 — 2026-05-08 11:45:28 UTC

- **Tx**：[`0x022f972a61ad513dd9b81b4fde8185cd3b61e7009e5f60b40b2a16b75dc610b2`](https://testnet.bscscan.com/tx/0x022f972a61ad513dd9b81b4fde8185cd3b61e7009e5f60b40b2a16b75dc610b2)
- **发起方**：`0xe376F3E7Fb15B526152F0db6805F1002564cbC2B`

| 参数 | 值 |
|------|----|
| `rejectVotes` | `2` |

### 🗳 `VoteCast` — Policy — 区块 106217140 — 2026-05-08 11:45:28 UTC

- **Tx**：[`0x022f972a61ad513dd9b81b4fde8185cd3b61e7009e5f60b40b2a16b75dc610b2`](https://testnet.bscscan.com/tx/0x022f972a61ad513dd9b81b4fde8185cd3b61e7009e5f60b40b2a16b75dc610b2)
- **发起方**：`0xe376F3E7Fb15B526152F0db6805F1002564cbC2B`

| 参数 | 值 |
|------|----|
| `voter` | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` |
| `rejectVotes` | `2` |

### 🏁 `JobFinalised` — Router — 区块 106217289 — 2026-05-08 11:46:36 UTC

- **Tx**：[`0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710`](https://testnet.bscscan.com/tx/0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710)
- **发起方**：`0xD8c45dA4e4036f4946132B18fc7568096CB7535f`

| 参数 | 值 |
|------|----|
| `status` | `4` (REJECTED) |

### ❌ `JobRejected` — Commerce — 区块 106217289 — 2026-05-08 11:46:36 UTC

- **Tx**：[`0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710`](https://testnet.bscscan.com/tx/0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710)
- **发起方**：`0xD8c45dA4e4036f4946132B18fc7568096CB7535f`

| 参数 | 值 |
|------|----|
| `rejector` | `0xD7d36D66d2F1B608A0F943f722D27e3744f66F25` |
| `reason` | `0x0e88376365f8d49567d29c83ad71d33b71b71a813676bc3d9e745d0c22f06ebb` |

### 🏁 `JobSettled` — Router — 区块 106217289 — 2026-05-08 11:46:36 UTC

- **Tx**：[`0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710`](https://testnet.bscscan.com/tx/0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710)
- **发起方**：`0xD8c45dA4e4036f4946132B18fc7568096CB7535f`

| 参数 | 值 |
|------|----|
| `policy` | `0x4F4678D4439feC812Ac7674Bb3Efb4C8f5Fb78A6` |
| `verdict` | `2` (REJECT) |
| `reason` | `0xb9b7ef8cad1a17a35730d4cfa27844586a1e7eb45522adb6013816c101cdf9f6` (OPTIMISTIC_REJECTED) |

### ↩️ `Refunded` — Commerce — 区块 106217289 — 2026-05-08 11:46:36 UTC

- **Tx**：[`0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710`](https://testnet.bscscan.com/tx/0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710)
- **发起方**：`0xD8c45dA4e4036f4946132B18fc7568096CB7535f`

| 参数 | 值 |
|------|----|
| `client` | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` |
| `amount` | `1000000000000000000` raw (1.0000 U) |

---

## Deliverable

- **deliverable_url**：`ipfs://QmW7FviqscbkCyQSUvzL6CCyrXd1RgDehwxanXXf3vGqQc`
- **CID**：`QmW7FviqscbkCyQSUvzL6CCyrXd1RgDehwxanXXf3vGqQc`
- **Gateway URL**：https://gateway.pinata.cloud/ipfs/QmW7FviqscbkCyQSUvzL6CCyrXd1RgDehwxanXXf3vGqQc
- **Manifest hash（链上 deliverable 字段）**：`0xe0b01f59e2f972a468df1949c825ac8159e5a725a9074db0b920485eedea11f8`

**Response 内容**（截断至 2 KB）：

```text
<拉取失败: HTTP Error 403: Forbidden>
```

---

## 资金流向

| 方向 | from | to | 金额 | 触发事件 |
|------|------|----|------|----------|
| 注入托管 | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` | `0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE` | 1.0000 U | `JobFunded` |
| 退款给客户端 | `0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE` | `0xe376F3E7Fb15B526152F0db6805F1002564cbC2B` | 1.0000 U | `Refunded` |

> **提供方实收**：0.0000 U
> **客户端净支出**：0.0000 U

---

## 完整交易索引

| 区块 | 时间 (UTC) | Tx Hash | 触发的事件 |
|------|------------|---------|------------|
| 106214238 | 2026-05-08 11:23:43 UTC | [`0xab794df936ba4c7bf88bb7130c21be3de7fcc64036c46c0a98642e1df82d64fb`](https://testnet.bscscan.com/tx/0xab794df936ba4c7bf88bb7130c21be3de7fcc64036c46c0a98642e1df82d64fb) | JobCreated |
| 106214242 | 2026-05-08 11:23:44 UTC | [`0xb5b0c52421a2ecc40faf5017c1bb7927b376f71eda64b86145fe498ac9704a58`](https://testnet.bscscan.com/tx/0xb5b0c52421a2ecc40faf5017c1bb7927b376f71eda64b86145fe498ac9704a58) | JobRegistered |
| 106214247 | 2026-05-08 11:23:47 UTC | [`0x6d2e0aced8adf3287a182144365d776c00dbc2e8b8d31cac47e717d9f7917577`](https://testnet.bscscan.com/tx/0x6d2e0aced8adf3287a182144365d776c00dbc2e8b8d31cac47e717d9f7917577) | BudgetSet |
| 106214253 | 2026-05-08 11:23:49 UTC | [`0xd3181c7ea8b11031cc54ee1ac3305a6124db50af224109d99338122fd93ccdbe`](https://testnet.bscscan.com/tx/0xd3181c7ea8b11031cc54ee1ac3305a6124db50af224109d99338122fd93ccdbe) | JobFunded |
| 106214333 | 2026-05-08 11:24:25 UTC | [`0xae7354cc62a1149ca6171371b104478e2e1bea2e3fa64a48e28910129d7eeea8`](https://testnet.bscscan.com/tx/0xae7354cc62a1149ca6171371b104478e2e1bea2e3fa64a48e28910129d7eeea8) | JobInitialised、JobSubmitted |
| 106214346 | 2026-05-08 11:24:31 UTC | [`0x067ee42252ce9b3f21f33515634fec695dd181a4ee893ae4c790b472ddbbaa21`](https://testnet.bscscan.com/tx/0x067ee42252ce9b3f21f33515634fec695dd181a4ee893ae4c790b472ddbbaa21) | Disputed |
| 106217099 | 2026-05-08 11:45:10 UTC | [`0xc6a6b7f5349f104a9043e55baf7cee2e7f95e0a553ca81f00e793499bd166e8e`](https://testnet.bscscan.com/tx/0xc6a6b7f5349f104a9043e55baf7cee2e7f95e0a553ca81f00e793499bd166e8e) | VoteCast |
| 106217140 | 2026-05-08 11:45:28 UTC | [`0x022f972a61ad513dd9b81b4fde8185cd3b61e7009e5f60b40b2a16b75dc610b2`](https://testnet.bscscan.com/tx/0x022f972a61ad513dd9b81b4fde8185cd3b61e7009e5f60b40b2a16b75dc610b2) | QuorumReached、VoteCast |
| 106217289 | 2026-05-08 11:46:36 UTC | [`0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710`](https://testnet.bscscan.com/tx/0x85102a08a8319b2cc48303f56392c0cc886883891c4ac2c149da321fd503d710) | JobFinalised、JobRejected、JobSettled、Refunded |
