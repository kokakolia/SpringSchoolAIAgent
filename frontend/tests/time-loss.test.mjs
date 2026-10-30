import test from "node:test";
import assert from "node:assert/strict";
import { TimeLossStore, resumeTimeLoss } from "../dist/time-loss.js";

function storage() {
  const data = new Map();
  return {
    getItem: (key) => data.get(key),
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  };
}
test("an event cannot start without durable identifiers for safe retry", () => {
  const store = new TimeLossStore(null);
  assert.throws(() => store.begin("project", 120), /хранилищу/);
  assert.equal(store.load("project"), null);
});
test("600 -> 480 -> failed generation -> retry stays 480 and only retries generation", async () => {
  const disk = storage();
  let store = new TimeLossStore(disk),
    balance = 600,
    losses = 0,
    generations = 0;
  store.begin("project", 120, () => crypto.randomUUID());
  const options = {
    getRevision: async () => losses,
    recordLoss: async () => {
      balance -= 120;
      losses++;
    },
    proposePlan: async () => {
      generations++;
      if (generations === 1) throw Error("AI failed");
    },
  };
  await assert.rejects(resumeTimeLoss(store, "project", options), /AI failed/);
  assert.equal(balance, 480);
  assert.equal(store.load("project").phase, "plan");
  store = new TimeLossStore(disk);
  store.begin("project", 120);
  await resumeTimeLoss(store, "project", options);
  assert.equal(balance, 480);
  assert.equal(losses, 1);
  assert.equal(generations, 2);
  assert.equal(store.load("project").phase, "refresh");
  await resumeTimeLoss(store, "project", options);
  assert.equal(generations, 2);
  store.clear("project");
  assert.equal(new TimeLossStore(disk).load("project"), null);
});
test("lost event and generation responses reuse identifiers after reload", async () => {
  const disk = storage();
  let store = new TimeLossStore(disk),
    balance = 600;
  const applied = new Set(),
    plans = new Set();
  let firstEvent = true,
    firstPlan = true;
  const original = store.begin("project", 120);
  const eventId = original.eventRequestId,
    planId = original.planRequestId;
  const options = {
    getRevision: async () => 1,
    recordLoss: async (body) => {
      assert.equal(body.request_id, eventId);
      if (!applied.has(body.request_id)) {
        applied.add(body.request_id);
        balance -= body.minutes;
      }
      if (firstEvent) {
        firstEvent = false;
        throw Error("Lost event response");
      }
    },
    proposePlan: async (body) => {
      assert.equal(body.request_id, planId);
      plans.add(body.request_id);
      if (firstPlan) {
        firstPlan = false;
        throw Error("Lost plan response");
      }
    },
  };
  await assert.rejects(resumeTimeLoss(store, "project", options));
  store = new TimeLossStore(disk);
  await assert.rejects(resumeTimeLoss(store, "project", options));
  store = new TimeLossStore(disk);
  await resumeTimeLoss(store, "project", options);
  assert.equal(balance, 480);
  assert.equal(applied.size, 1);
  assert.equal(plans.size, 1);
});
