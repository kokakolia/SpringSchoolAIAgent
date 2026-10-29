import test from "node:test";
import assert from "node:assert/strict";
import { ComposerDraftStore } from "../dist/composer.js";

function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
  };
}
test("messages remain separate for each project and survive reload", () => {
  const data = storage(),
    drafts = new ComposerDraftStore(data);
  drafts.save("a", "Keep animation");
  drafts.save("b", "Review API");
  const restored = new ComposerDraftStore(data);
  assert.equal(restored.load("a"), "Keep animation");
  assert.equal(restored.load("b"), "Review API");
  restored.clear("a");
  assert.equal(new ComposerDraftStore(data).load("a"), "");
  assert.equal(restored.load("b"), "Review API");
});
test("expired drafts disappear and unavailable storage still works for the session", () => {
  let now = 100;
  const drafts = new ComposerDraftStore(null, () => now);
  drafts.save("a", "A message");
  assert.equal(drafts.load("a"), "A message");
  now += 24 * 60 * 60 * 1000;
  assert.equal(drafts.load("a"), "");
  drafts.save("b", "New");
  assert.equal(drafts.load("b"), "New");
});
test("corrupt storage cannot break the composer and retention is bounded", () => {
  const drafts = new ComposerDraftStore({ getItem: () => "not JSON" });
  for (let i = 0; i < 50; i++) drafts.save(`p${i}`, "x".repeat(9000));
  assert.ok(Object.keys(drafts.records).length <= 30);
  assert.ok(
    Object.values(drafts.records).every((record) => record.text.length <= 8000),
  );
});
