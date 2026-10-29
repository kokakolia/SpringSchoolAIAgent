import assert from "node:assert/strict";
import test from "node:test";
import {
  AcceptedNoticeStore,
  displayedPlan,
  pendingPlan,
  samePlan,
} from "../dist/view-state.js";

const proposal = () => ({
  summary: "Plan",
  buffer_minutes: 15,
  tasks: [
    {
      id: "review",
      title: "Review",
      estimate_minutes: 40,
      priority: "must",
      decision: "keep",
      done_when: "Works",
      depends_on: [],
      constraint_refs: [1, 2],
      reason: "Why",
    },
  ],
});
const record = (id, plan = proposal()) => ({
  id,
  proposal: plan,
  validation: { valid: true },
});

test("an unaccepted proposal does not disable the accepted task list", () => {
  const draft = proposal();
  draft.tasks[0].estimate_minutes = 50;
  const board = { accepted: record("accepted"), draft: record("draft", draft) };
  assert.equal(displayedPlan(board).id, "accepted");
  assert.equal(displayedPlan(board, "changes").id, "draft");
  assert.equal(displayedPlan({ draft: board.draft }).id, "draft");
});
test("new explanations and reference order do not require accepting the same work", () => {
  const revised = proposal();
  revised.summary = "Different summary";
  revised.tasks[0].reason = "Different reason";
  revised.tasks[0].constraint_refs.reverse();
  assert.ok(samePlan(proposal(), revised));
  assert.equal(
    pendingPlan({
      accepted: record("accepted"),
      draft: record("draft", revised),
    }),
    null,
  );
});
test("scope, priority, estimates, dependencies, order, and reserve still count as changes", () => {
  for (const [field, value] of [
    ["done_when", "A different result"],
    ["priority", "could"],
    ["estimate_minutes", 50],
    ["decision", "defer"],
    ["depends_on", ["other"]],
  ]) {
    const revised = proposal();
    revised.tasks[0][field] = value;
    assert.equal(samePlan(proposal(), revised), false, field);
  }
  const revised = proposal();
  revised.buffer_minutes++;
  assert.equal(samePlan(proposal(), revised), false);
});
test("invalid or first proposals remain visible for clarification and acceptance", () => {
  const draft = record("draft");
  draft.validation.valid = false;
  assert.equal(pendingPlan({ accepted: record("accepted"), draft }), draft);
  assert.equal(pendingPlan({ draft: record("first") }).id, "first");
});
test("accepted notice stays dismissed after reload, separately for each project and version", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
  };
  new AcceptedNoticeStore(storage).dismiss("project-a", "version-1");
  const restored = new AcceptedNoticeStore(storage);
  assert.ok(restored.isDismissed("project-a", "version-1"));
  assert.equal(restored.isDismissed("project-b", "version-1"), false);
  assert.equal(restored.isDismissed("project-a", "version-2"), false);
});
test("notice dismissal works in memory if browser storage is unavailable", () => {
  const store = new AcceptedNoticeStore({
    getItem() {
      throw Error();
    },
    setItem() {
      throw Error();
    },
  });
  store.dismiss("project", "version");
  assert.ok(store.isDismissed("project", "version"));
});
