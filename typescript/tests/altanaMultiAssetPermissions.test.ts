import { getAddress } from "viem";
import { describe, expect, it } from "vitest";
import { NETWORKS } from "../src/config.js";
import { AssetId, getAsset } from "../src/networks/assets.js";
import {
  DEFAULT_NATIVE_GAS_ALLOWANCE_WEI,
  defaultAgentPermissions,
} from "../src/wallets/altana/permissions.js";

describe("Altana multi-asset session permissions", () => {
  it("allows the exact createJobWithToken selector on Commerce", () => {
    const permissions = defaultAgentPermissions({
      chainId: 97,
      tokenSpend: { limit: 1n },
      roles: ["buyer"],
    });
    expect(permissions.calls).toContainEqual({
      to: getAddress(NETWORKS["bsc-testnet"].commerceContract),
      signature:
        "createJobWithToken(address,address,uint256,string,address,address)",
    });
    expect(
      permissions.calls?.some((call) => "to" in call && !("signature" in call)),
    ).toBe(false);
  });

  it("expresses independent catalog-token caps", () => {
    const u = getAsset(97, AssetId.TEST_U);
    const usdc = getAsset(97, AssetId.TEST_USDC);
    const usdt = getAsset(97, AssetId.TEST_USDT);
    const permissions = defaultAgentPermissions({
      chainId: 97,
      tokenSpends: [
        { token: u.address, limit: 10n },
        { token: usdc.address, limit: 20n, period: "week" },
        { token: usdt.address, limit: 30n },
      ],
    });

    expect(permissions.spend).toEqual([
      { token: u.address, limit: 10n, period: "day" },
      { token: usdc.address, limit: 20n, period: "week" },
      { token: usdt.address, limit: 30n, period: "day" },
      { limit: DEFAULT_NATIVE_GAS_ALLOWANCE_WEI, period: "day" },
    ]);
  });

  it("fails closed on ambiguous, unknown, duplicate, non-checksum and invalid caps", () => {
    const usdc = getAsset(97, AssetId.TEST_USDC);
    const unknown = getAddress(`0x${"99".repeat(20)}`);

    expect(() =>
      defaultAgentPermissions({
        chainId: 97,
        tokenSpend: { limit: 1n },
        tokenSpends: [{ token: usdc.address, limit: 1n }],
      }),
    ).toThrow(/exactly one of tokenSpend or tokenSpends/);
    expect(() =>
      defaultAgentPermissions({
        chainId: 97,
        tokenSpends: [{ token: unknown, limit: 1n }],
      }),
    ).toThrow(/registered catalog token/);
    expect(() =>
      defaultAgentPermissions({
        chainId: 97,
        tokenSpends: [
          { token: usdc.address, limit: 1n },
          { token: usdc.address, limit: 2n },
        ],
      }),
    ).toThrow(/duplicate token spend cap/);
    expect(() =>
      defaultAgentPermissions({
        chainId: 97,
        tokenSpends: [
          {
            token: usdc.address.toLowerCase() as `0x${string}`,
            limit: 1n,
          },
        ],
      }),
    ).toThrow(/checksummed/);
    expect(() =>
      defaultAgentPermissions({
        chainId: 97,
        tokenSpends: [{ token: usdc.address, limit: -1n }],
      }),
    ).toThrow(/non-negative bigint/);
    expect(() =>
      defaultAgentPermissions({ chainId: 97, tokenSpends: [] }),
    ).toThrow(/non-empty/);
    expect(() =>
      defaultAgentPermissions({
        chainId: 97,
        tokenSpends: [
          { token: usdc.address, limit: 1n, period: "forever" as never },
        ],
      }),
    ).toThrow(/unsupported spend period/);
  });

  it("forbids calls to every catalog token while keeping legacy config", () => {
    const usdc = getAsset(97, AssetId.TEST_USDC);
    expect(() =>
      defaultAgentPermissions({
        chainId: 97,
        tokenSpend: { limit: 1n },
        extraCalls: [
          { to: usdc.address, signature: "approve(address,uint256)" },
        ],
      }),
    ).toThrow(/session calls to catalog payment tokens are forbidden/);
  });
});
