import { describe, expect, it } from "vitest";
import {
  canonicalJson,
  keccakOfCanonicalJson,
  keccakOfText,
} from "../src/core/canonicalJson.js";

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

  it("matches Python for empty objects and arrays", () => {
    // python3 -c "import json; print(json.dumps({'a':{},'b':[]},
    //   sort_keys=True, separators=(',',':')))" -> {"a":{},"b":[]}
    expect(canonicalJson({ a: {}, b: [] })).toBe('{"a":{},"b":[]}');
  });

  it("serializes plain integers the JS way (no Python-style .0 float tail)", () => {
    // JS has one `number` type: 2 === 2.0, so there is no way to recover
    // "the caller meant a float" the way Python's json.dumps(2.0) -> "2.0"
    // does. This documents the resulting (intentional) divergence.
    expect(canonicalJson({ a: 2 })).toBe('{"a":2}');
  });

  it("throws instead of silently emitting null for non-finite numbers", () => {
    // JSON.stringify(NaN) === "null", which would silently diverge from
    // Python's json.dumps(float('nan')) -> "NaN". Fail loud instead.
    expect(() => canonicalJson({ a: Number.NaN })).toThrow(TypeError);
    expect(() => canonicalJson({ a: Number.POSITIVE_INFINITY })).toThrow(
      TypeError,
    );
    expect(() => canonicalJson({ a: Number.NEGATIVE_INFINITY })).toThrow(
      TypeError,
    );
  });
});
