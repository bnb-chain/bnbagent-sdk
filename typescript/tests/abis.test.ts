import { toFunctionSelector } from "viem";
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

  it("publishes the multi-token Commerce surface and selectors", () => {
    const createJobWithToken = agenticCommerceAbi.find(
      (entry) =>
        entry.type === "function" && entry.name === "createJobWithToken",
    );
    expect(createJobWithToken).toBeDefined();
    expect(
      createJobWithToken?.type === "function"
        ? createJobWithToken.inputs.map((input) => input.type)
        : [],
    ).toEqual([
      "address",
      "address",
      "uint256",
      "string",
      "address",
      "address",
    ]);
    expect(
      toFunctionSelector(
        "createJobWithToken(address,address,uint256,string,address,address)",
      ),
    ).toBe("0xe1623ca4");

    const names = new Set(
      agenticCommerceAbi.map((entry) => "name" in entry && entry.name),
    );
    expect(names.has("jobPaymentToken")).toBe(true);
    expect(names.has("isPaymentTokenSupported")).toBe(true);
    expect(names.has("JobPaymentTokenBound")).toBe(true);
  });

  it("keeps the legacy create selector and Job tuple layout compatible", () => {
    expect(
      toFunctionSelector("createJob(address,address,uint256,string,address)"),
    ).toBe("0x41528812");

    const expectedJobFields = [
      ["id", "uint256"],
      ["client", "address"],
      ["provider", "address"],
      ["evaluator", "address"],
      ["description", "string"],
      ["budget", "uint256"],
      ["expiredAt", "uint256"],
      ["status", "uint8"],
      ["hook", "address"],
      ["submittedAt", "uint256"],
      ["deliverable", "bytes32"],
    ];
    const jobs = agenticCommerceAbi.find(
      (entry) => entry.type === "function" && entry.name === "jobs",
    );
    expect(
      jobs?.type === "function"
        ? jobs.outputs.map((output) => [output.name, output.type])
        : [],
    ).toEqual(expectedJobFields);

    const getJob = agenticCommerceAbi.find(
      (entry) => entry.type === "function" && entry.name === "getJob",
    );
    expect(
      getJob?.type === "function" && getJob.outputs[0]?.type === "tuple"
        ? getJob.outputs[0].components?.map((component) => [
            component.name,
            component.type,
          ])
        : [],
    ).toEqual(expectedJobFields);
  });
});
