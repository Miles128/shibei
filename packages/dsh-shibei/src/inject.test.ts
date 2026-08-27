import assert from "node:assert/strict";
import test from "node:test";
import { formatContextPack, shouldSkipInstruction } from "./inject.ts";

test("formatContextPack truncates to maxInjectedBytes", () => {
  const pack = {
    query_used: "Redis",
    results: [
      { source: "a.md", text: "x".repeat(5000), score: 1 },
    ],
    memories: [{ category: "user", text: "喜欢 Python" }],
  };
  const md = formatContextPack(pack, 4096);
  assert.ok(Buffer.byteLength(md, "utf8") <= 4096);
  assert.ok(md.includes("拾贝上下文"));
  assert.ok(md.includes("喜欢 Python"));
});

test("shouldSkipInstruction skips short commands", () => {
  assert.equal(shouldSkipInstruction("改配置", true), true);
  assert.equal(shouldSkipInstruction("我笔记里 Redis 怎么写的", true), false);
  assert.equal(shouldSkipInstruction("跑测试", true), true);
});
