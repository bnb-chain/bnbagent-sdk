import { describe, expect, it } from "vitest";
import { agenticCommerceAbi } from "../src/abis/agenticCommerce.js";
import { erc20Abi } from "../src/abis/erc20.js";
import { evaluatorRouterAbi } from "../src/abis/evaluatorRouter.js";
import { identityRegistryAbi } from "../src/abis/identityRegistry.js";
import { optimisticPolicyAbi } from "../src/abis/optimisticPolicy.js";

describe("generated ABIs", () => {
  it.each([
    ["agenticCommerceAbi", agenticCommerceAbi],
    ["erc20Abi", erc20Abi],
    ["evaluatorRouterAbi", evaluatorRouterAbi],
    ["identityRegistryAbi", identityRegistryAbi],
    ["optimisticPolicyAbi", optimisticPolicyAbi],
  ])("%s is a non-empty array", (_name, abi) => {
    expect(Array.isArray(abi)).toBe(true);
    expect(abi.length).toBeGreaterThan(0);
  });

  it("agenticCommerceAbi contains a createJob function entry", () => {
    const createJob = agenticCommerceAbi.find(
      (entry) => entry.type === "function" && entry.name === "createJob",
    );
    expect(createJob).toBeDefined();
  });
});
