import assert from "node:assert/strict";
import test from "node:test";
import { Demo } from "../dist/demo.js";

test("reopening an over-budget task returns only the original available balance", (t) => {
  storage(t);
  const demo = new Demo();
  demo.loseTime(355);
  demo.task("api", { done: true });
  assert.equal(demo.data.project.state.available_minutes, 0);
  demo.task("api", { done: false });
  assert.equal(demo.data.project.state.available_minutes, 5);
});

function storage(t) {
  const previous = globalThis.localStorage,
    values = new Map();
  globalThis.localStorage = {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
  };
  t.after(() => {
    globalThis.localStorage = previous;
  });
}
test("renaming persists while preserving the project, plan and conversation", (t) => {
  storage(t);
  const demo = new Demo(),
    before = structuredClone(demo.data);
  demo.renameProject("  New name  ");
  const restored = new Demo();
  assert.equal(restored.list()[0].title, "New name");
  assert.deepEqual(restored.data.accepted, before.accepted);
  assert.deepEqual(restored.data.messages, before.messages);
  assert.equal(restored.data.project.id, before.project.id);
});
test("renaming does not make an actionable proposal stale", (t) => {
  storage(t);
  const demo = new Demo();
  demo.loseTime();
  demo.propose("Пересобери план");
  const draft = demo.workspace().draft;
  assert.ok(draft);
  demo.renameProject("Rename with pending plan");
  assert.equal(demo.workspace().draft_stale, false);
  demo.accept();
  assert.equal(demo.workspace().accepted.id, draft.id);
});
test("deletion remains empty after reload and opening the example restores a usable project", (t) => {
  storage(t);
  const demo = new Demo();
  demo.deleteProject();
  const restored = new Demo();
  assert.deepEqual(restored.list(), []);
  assert.equal(restored.workspace(), null);
  assert.deepEqual(restored.data.messages, []);
  restored.restoreExample();
  assert.equal(restored.list().length, 1);
  assert.ok(restored.workspace().accepted);
});

test("demo preserves losses beyond zero when a completed task is reopened", (t) => {
  storage(t);
  const demo = new Demo();
  demo.loseTime(120);
  demo.task("api", { done: true });
  assert.equal(demo.data.project.state.available_minutes, 200);
  demo.loseTime(220);
  assert.equal(demo.data.project.state.available_minutes, 0);
  demo.task("api", { done: false });
  assert.equal(demo.data.project.state.available_minutes, 20);
});
