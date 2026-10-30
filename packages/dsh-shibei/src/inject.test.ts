import assert from "node:assert/strict";
import test from "node:test";
import {
  formatAutoMemoryNotice,
  formatContextPack,
  formatPersona,
  formatRememberHint,
  handleMemoryCommand,
  handleShibeiCommand,
  parseHostLlmTerms,
  shouldSkipInstruction,
} from "./inject.ts";

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

test("formatPersona lists facts by category", () => {
  const md = formatPersona({
    total: 2,
    categories: {
      preference: [{ fact: "偏好 Python" }],
      user: [{ fact: "住在上海" }],
    },
  });
  assert.ok(md.includes("[拾贝画像]"));
  assert.ok(md.includes("偏好 Python"));
  assert.ok(md.includes("住在上海"));
});

test("formatPersona empty", () => {
  assert.equal(formatPersona({ total: 0, categories: {} }), "[拾贝画像]\n暂无长期记忆。");
});

test("formatRememberHint asks model to call remember", () => {
  const md = formatRememberHint({
    should: true,
    fact: "用户更喜欢 Python",
    reason: "short_first_person",
  });
  assert.ok(md.includes("shibei_remember"));
  assert.ok(md.includes("用户更喜欢 Python"));
  assert.equal(formatRememberHint({ should: false, fact: "", reason: "" }), "");
});

test("parseHostLlmTerms keeps 5-8 keywords", () => {
  const q = parseHostLlmTerms("1. Redis\n2. 缓存\n3. 面试\n4. 布隆\n5. 过滤器\n6. Python\n7. Go\n8. 偏好\n9. extra");
  const terms = q.split(/\s+/).filter(Boolean);
  assert.ok(terms.length >= 5 && terms.length <= 8);
  assert.ok(terms.includes("Redis"));
  assert.ok(!terms.includes("extra"));
});

test("handleShibeiCommand searches or shows usage", async () => {
  const calls: string[] = [];
  const usage = await handleShibeiCommand("", {
    search: async () => ({ results: [] }),
    persona: async () => ({ total: 0, categories: {} }),
  });
  assert.ok(usage.includes("/shibei"));
  const out = await handleShibeiCommand("Redis 缓存", {
    search: async (q) => {
      calls.push(q);
      return { query_used: q, results: [{ source: "a.md", text: "布隆过滤器" }], memories: [] };
    },
    persona: async () => ({ total: 0, categories: {} }),
  });
  assert.deepEqual(calls, ["Redis 缓存"]);
  assert.ok(out.includes("布隆过滤器"));
});

test("formatAutoMemoryNotice lists written facts or stays empty", () => {
  const md = formatAutoMemoryNotice([
    { fact: "偏好 Python 而非 Go" },
    { fact: "" },
    { fact: "正在找长三角的金融 AI 岗位" },
  ]);
  assert.ok(md.includes("[拾贝自动记忆]"));
  assert.ok(md.includes("shibei_remember"));
  assert.ok(md.includes("偏好 Python 而非 Go"));
  assert.ok(md.includes("正在找长三角的金融 AI 岗位"));
  assert.equal(formatAutoMemoryNotice([]), "");
  assert.equal(formatAutoMemoryNotice([{ fact: "" }, {}]), "");

  const capped = formatAutoMemoryNotice(
    Array.from({ length: 7 }, (_, i) => ({ fact: `事实${i + 1}` })),
  );
  assert.ok(capped.includes("事实5"));
  assert.ok(!capped.includes("事实6")); // 超过 5 条不注入
});

test("handleMemoryCommand forgets by id when supported", async () => {
  const forgotten: string[] = [];
  const out = await handleMemoryCommand("forget #12", {
    list: async () => [],
    remember: async (fact) => ({ fact }),
    search: async () => [],
    forget: async (id) => {
      forgotten.push(id);
      return true;
    },
  });
  assert.deepEqual(forgotten, ["12"]);
  assert.ok(out.includes("#12"));

  const missing = await handleMemoryCommand("forget 99", {
    list: async () => [],
    remember: async (fact) => ({ fact }),
    search: async () => [],
    forget: async () => false,
  });
  assert.ok(missing.includes("未找到"));

  const noSupport = await handleMemoryCommand("forget 12", {
    list: async () => [],
    remember: async (fact) => ({ fact }),
    search: async () => [],
  });
  assert.ok(noSupport.includes("不支持"));
});

test("handleMemoryCommand list shows ids and forget hint", async () => {
  const out = await handleMemoryCommand("", {
    list: async () => [{ id: "7", category: "user", fact: "喜欢茶" }],
    remember: async (fact) => ({ fact }),
    search: async () => [],
  });
  assert.ok(out.includes("#7"));
  assert.ok(out.includes("forget"));
  assert.ok(out.includes("喜欢茶"));
});

test("handleMemoryCommand lists, remembers, or searches", async () => {
  const remembered: string[] = [];
  const listed = await handleMemoryCommand("", {
    list: async () => [{ fact: "喜欢茶" }],
    remember: async (fact) => {
      remembered.push(fact);
      return { fact };
    },
    search: async () => [{ text: "喜欢 Python" }],
  });
  assert.ok(listed.includes("喜欢茶"));

  const added = await handleMemoryCommand("remember 更喜欢 Rust", {
    list: async () => [],
    remember: async (fact) => {
      remembered.push(fact);
      return { fact };
    },
    search: async () => [],
  });
  assert.deepEqual(remembered, ["更喜欢 Rust"]);
  assert.ok(added.includes("更喜欢 Rust"));

  const found = await handleMemoryCommand("Python", {
    list: async () => [],
    remember: async (fact) => ({ fact }),
    search: async () => [{ text: "偏好 Python" }],
  });
  assert.ok(found.includes("偏好 Python"));
});
