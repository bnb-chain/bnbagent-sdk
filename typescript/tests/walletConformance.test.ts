/**
 * Base-class wallet conformance tests (design doc §3.3 / §3.4), part 1.
 *
 * Mirrors the base-class portions of
 * `python/tests/test_wallet_conformance.py`: sign.* auto-derivation from
 * method overrides (including the override-is-capability counterexample),
 * `extraCapabilities` union, `supports()` as a pure membership test,
 * `describe()` shape, and the `makeExecutor()` construction-time capability
 * gate. EVM/TWAK-specific conformance belongs to later tasks.
 */

import { describe, expect, it } from "vitest";
import {
  BROADCAST_SELF,
  SIGN_MESSAGE,
  SIGN_TRANSACTION,
  SIGN_TYPED_DATA,
  UnsupportedWalletOperation,
  WalletIdentityMismatch,
  WalletProvider,
} from "../src/wallets/index.js";
import type {
  ExecutionContext,
  SignatureResult,
} from "../src/wallets/index.js";

const DUMMY_SIGNATURE: SignatureResult = {
  messageHash: `0x${"00".repeat(32)}`,
  r: `0x${"00".repeat(32)}`,
  s: `0x${"00".repeat(32)}`,
  v: 27n,
  signature: `0x${"00".repeat(65)}`,
};

/** Minimal constructible provider: only the abstract `address` getter. */
class AddressOnlyWallet extends WalletProvider {
  get address(): `0x${string}` {
    return `0x${"11".repeat(20)}`;
  }
}

/** A dummy ExecutionContext — `client` is never actually used by the
 * capability-gate path under test, so an empty object stub is sufficient. */
function makeExecutionContext(): ExecutionContext {
  return { client: {} as ExecutionContext["client"] };
}

describe("WalletProvider — sign.* auto-derivation", () => {
  it("address-only subclass has no capabilities", () => {
    expect(new AddressOnlyWallet().capabilities()).toEqual(new Set());
  });

  it("overriding signMessage derives exactly {sign.message}", () => {
    class MessageOnly extends AddressOnlyWallet {
      override async signMessage(_message: string): Promise<SignatureResult> {
        return DUMMY_SIGNATURE;
      }
    }
    expect(new MessageOnly().capabilities()).toEqual(new Set([SIGN_MESSAGE]));
  });

  it("override-to-raise still claims the capability (don't-override-to-raise counterexample)", () => {
    // Derivation only sees that the method was overridden, not what it
    // does — so overriding signTypedData just to raise FALSELY claims
    // sign.typed_data. This is exactly why the discipline says "don't
    // override-to-raise": not overriding is the only way to keep the
    // capability out of capabilities().
    class OverridesToRaise extends AddressOnlyWallet {
      override async signTypedData(): Promise<SignatureResult> {
        throw new UnsupportedWalletOperation(SIGN_TYPED_DATA);
      }
    }
    expect(new OverridesToRaise().capabilities().has(SIGN_TYPED_DATA)).toBe(
      true,
    );
  });

  it("extraCapabilities unions with the derived set, including vendor-namespaced values", () => {
    class ExtraCapWallet extends AddressOnlyWallet {
      protected override readonly extraCapabilities: ReadonlySet<string> =
        new Set([BROADCAST_SELF, "acme.batch_sign"]);
      override async signMessage(_message: string): Promise<SignatureResult> {
        return DUMMY_SIGNATURE;
      }
    }
    const wallet = new ExtraCapWallet();
    expect(wallet.capabilities()).toEqual(
      new Set([SIGN_MESSAGE, BROADCAST_SELF, "acme.batch_sign"]),
    );
    // Open set: the vendor-namespaced value is first-class in supports()...
    expect(wallet.supports("acme.batch_sign")).toBe(true);
    // ...and any string not declared is simply unsupported.
    expect(wallet.supports("acme.other")).toBe(false);
  });
});

describe("WalletProvider#supports", () => {
  it("is a pure membership test over capabilities(); unknown values are false, never throw", () => {
    const wallet = new AddressOnlyWallet();
    expect(wallet.supports(SIGN_MESSAGE)).toBe(false);
    expect(() => wallet.supports("unknown.cap")).not.toThrow();
    expect(wallet.supports("unknown.cap")).toBe(false);
  });
});

describe("WalletProvider#describe", () => {
  it("returns sorted capabilities, kind, keyLocation, exists — no key material", () => {
    class ExtraCapWallet extends AddressOnlyWallet {
      protected override readonly extraCapabilities: ReadonlySet<string> =
        new Set(["zzz.last", BROADCAST_SELF]);
      override async signMessage(_message: string): Promise<SignatureResult> {
        return DUMMY_SIGNATURE;
      }
    }
    const description = new ExtraCapWallet().describe();
    expect(description.capabilities).toEqual(
      [...new Set([SIGN_MESSAGE, BROADCAST_SELF, "zzz.last"])].sort(),
    );
    expect(description.kind).toBe("custom");
    expect(description.keyLocation).toBeNull();
    expect(description.exists).toBe(true);
    expect(Object.keys(description).sort()).toEqual(
      ["address", "capabilities", "exists", "keyLocation", "kind"].sort(),
    );
  });

  it("reports a null address when the address getter throws", () => {
    class ThrowingAddress extends WalletProvider {
      get address(): `0x${string}` {
        throw new Error("address unavailable");
      }
    }
    expect(new ThrowingAddress().describe().address).toBeNull();
  });
});

describe("WalletProvider#makeExecutor", () => {
  it("throws UnsupportedWalletOperation naming sign.transaction at construction time on a signless subclass", () => {
    const wallet = new AddressOnlyWallet();
    let thrown: unknown;
    try {
      wallet.makeExecutor(makeExecutionContext());
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(UnsupportedWalletOperation);
    expect((thrown as Error).message).toContain(SIGN_TRANSACTION);
  });

  it("gate message names makeExecutor() in camelCase, not the Python snake_case make_executor()", () => {
    const wallet = new AddressOnlyWallet();
    let thrown: unknown;
    try {
      wallet.makeExecutor(makeExecutionContext());
    } catch (error) {
      thrown = error;
    }
    const message = (thrown as Error).message;
    expect(message).toContain("makeExecutor()");
    expect(message).not.toContain("make_executor");
  });
});

describe("WalletProvider — default sign.* methods", () => {
  it("signTransaction throws UnsupportedWalletOperation naming sign.transaction", async () => {
    const wallet = new AddressOnlyWallet();
    await expect(async () =>
      wallet.signTransaction({
        to: `0x${"22".repeat(20)}`,
        value: 0n,
        gas: 21000n,
        gasPrice: 10n ** 9n,
        nonce: 0,
        chainId: 56,
      } as never),
    ).rejects.toBeInstanceOf(UnsupportedWalletOperation);
    let thrown: unknown;
    try {
      await wallet.signTransaction({} as never);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(Error);
    expect((thrown as Error).message).toContain(SIGN_TRANSACTION);
  });

  it("signMessage throws UnsupportedWalletOperation naming sign.message", async () => {
    const wallet = new AddressOnlyWallet();
    let thrown: unknown;
    try {
      await wallet.signMessage("hello");
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(UnsupportedWalletOperation);
    expect(thrown).toBeInstanceOf(Error);
    expect((thrown as Error).message).toContain(SIGN_MESSAGE);
  });

  it("signTypedData throws UnsupportedWalletOperation naming sign.typed_data", async () => {
    const wallet = new AddressOnlyWallet();
    let thrown: unknown;
    try {
      await wallet.signTypedData(
        { name: "Conformance", version: "1", chainId: 56 },
        { Probe: [{ name: "value", type: "uint256" }] },
        { value: 1 },
      );
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(UnsupportedWalletOperation);
    expect(thrown).toBeInstanceOf(Error);
    expect((thrown as Error).message).toContain(SIGN_TYPED_DATA);
  });
});

describe("WalletProvider — default sign.* methods reject asynchronously", () => {
  // Regression: the default sign.* methods declare `Promise<T>` return
  // types but used to `throw` synchronously, so a caller composing
  // `wallet.signX(...).catch(...)` got an uncaught sync throw instead of a
  // rejected promise. Asserting on the bare call expression (no `async () =>`
  // wrapper, no try/catch) is exactly what would have thrown synchronously
  // before the fix — the assignment line itself must not throw.

  it("signMessage returns a rejecting promise rather than throwing synchronously", async () => {
    const wallet = new AddressOnlyWallet();
    const p = wallet.signMessage("x");
    await expect(p).rejects.toBeInstanceOf(UnsupportedWalletOperation);
  });

  it("signTransaction returns a rejecting promise rather than throwing synchronously", async () => {
    const wallet = new AddressOnlyWallet();
    const p = wallet.signTransaction({} as never);
    await expect(p).rejects.toBeInstanceOf(UnsupportedWalletOperation);
  });

  it("signTypedData returns a rejecting promise rather than throwing synchronously", async () => {
    const wallet = new AddressOnlyWallet();
    const p = wallet.signTypedData(
      { name: "Conformance", version: "1", chainId: 56 },
      { Probe: [{ name: "value", type: "uint256" }] },
      { value: 1 },
    );
    await expect(p).rejects.toBeInstanceOf(UnsupportedWalletOperation);
  });
});

describe("UnsupportedWalletOperation", () => {
  it("uses the first argument verbatim as the message when no options are given", () => {
    const error = new UnsupportedWalletOperation("sign.transaction");
    expect(error.message).toBe("sign.transaction");
  });

  it("assembles '<cap>: <reason>.' when only reason is given", () => {
    const error = new UnsupportedWalletOperation("sign.transaction", {
      reason: "no local signer configured",
    });
    expect(error.message).toBe("sign.transaction: no local signer configured.");
  });

  it("appends ' Alternative: <alt>.' when reason and alternative are given", () => {
    const error = new UnsupportedWalletOperation("sign.transaction", {
      reason: "no local signer configured",
      alternative: "use a signing-capable wallet",
    });
    expect(error.message).toBe(
      "sign.transaction: no local signer configured. Alternative: use a signing-capable wallet.",
    );
  });

  it("appends ' (ref: <ref>)' when reason, alternative and ref are all given", () => {
    const error = new UnsupportedWalletOperation("sign.transaction", {
      reason: "no local signer configured",
      alternative: "use a signing-capable wallet",
      ref: "docs/wallets.md#sign-transaction",
    });
    expect(error.message).toBe(
      "sign.transaction: no local signer configured. Alternative: use a signing-capable wallet. (ref: docs/wallets.md#sign-transaction)",
    );
  });

  it("is an instance of Error", () => {
    expect(new UnsupportedWalletOperation("x")).toBeInstanceOf(Error);
    expect(new UnsupportedWalletOperation("x").name).toBe(
      "UnsupportedWalletOperation",
    );
  });
});

describe("WalletIdentityMismatch", () => {
  it("exposes expected/actual fields and a descriptive message", () => {
    const expected = `0x${"aa".repeat(20)}`;
    const actual = `0x${"bb".repeat(20)}`;
    const error = new WalletIdentityMismatch({ expected, actual });
    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("WalletIdentityMismatch");
    expect(error.expected).toBe(expected);
    expect(error.actual).toBe(actual);
    expect(error.message).toContain(expected);
    expect(error.message).toContain(actual);
    expect(error.message).toContain("wallet identity mismatch");
  });
});
