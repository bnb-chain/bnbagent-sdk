import { describe, expect, it } from "vitest";
import {
  canonicalJson,
  keccakOfCanonicalJson,
  keccakOfText,
} from "../src/core/canonicalJson";

describe("canonicalJson", () => {
  it("sorts keys recursively and compacts", () => {
    expect(canonicalJson({ b: 1, a: { d: 2, c: [3, { f: 4, e: 5 }] } })).toBe(
      '{"a":{"c":[3,{"e":5,"f":4}],"d":2},"b":1}',
    );
  });

  it("ensure_ascii parity with Python", () => {
    // python: json.dumps({"a":"中"}, sort_keys=True, separators=(",",":"))
    expect(canonicalJson({ a: "中" })).toBe('{"a":"\\u4e2d"}');
    // python escapes 0x7f (DEL) too, not just >0x7e
    expect(canonicalJson({ a: "\u007f" })).toBe('{"a":"\\u007f"}');
    // astral char -> UTF-16 surrogate pair, each escaped separately
    expect(canonicalJson({ a: "😀" })).toBe('{"a":"\\ud83d\\ude00"}');
  });

  it("keccak matches Web3.keccak(text=...)", () => {
    // python3 -c "from web3 import Web3; v = Web3.keccak(text='{\"a\":1}').hex();
    //   print(v if v.startswith('0x') else '0x' + v)"
    // -> 0x25e7c2a96531eb50246780c1f25742e489bf55210e26981dc02992bb585feb97
    expect(keccakOfText('{"a":1}')).toBe(
      "0x25e7c2a96531eb50246780c1f25742e489bf55210e26981dc02992bb585feb97",
    );
    expect(keccakOfCanonicalJson({ a: 1 })).toBe(
      "0x25e7c2a96531eb50246780c1f25742e489bf55210e26981dc02992bb585feb97",
    );
  });

  it("escapes control characters below 0x20 same as Python", () => {
    // python: json.dumps({"a":"\x1f"}, sort_keys=True, separators=(",",":"))
    //   -> {"a":"\u001f"}
    expect(canonicalJson({ a: "\u001f" })).toBe('{"a":"\\u001f"}');
  });

  it("handles embedded quotes, backslashes, and standard escapes like Python", () => {
    // python: json.dumps({"a":"\""}, ...) -> {"a":"\""}
    expect(canonicalJson({ a: '"' })).toBe('{"a":"\\""}');
    // python: json.dumps({"a":"\\"}, ...) -> {"a":"\\"}
    expect(canonicalJson({ a: "\\" })).toBe('{"a":"\\\\"}');
    // python: json.dumps({"a":"\n\t\r"}, ...) -> {"a":"\n\t\r"}
    expect(canonicalJson({ a: "\n\t\r" })).toBe('{"a":"\\n\\t\\r"}');
  });

  it("does not escape forward slash, matching Python's default", () => {
    // python: json.dumps({"a":"/"}, ...) -> {"a":"/"}
    expect(canonicalJson({ a: "/" })).toBe('{"a":"/"}');
  });

  it("handles null, booleans, and numbers without extra whitespace", () => {
    expect(canonicalJson({ a: null, b: true, c: false, d: 0 })).toBe(
      '{"a":null,"b":true,"c":false,"d":0}',
    );
  });
});
