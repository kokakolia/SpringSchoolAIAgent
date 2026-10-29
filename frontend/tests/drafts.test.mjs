import test from "node:test";
import assert from "node:assert/strict";
import {
  ProjectDraftStore,
  PROJECT_DRAFT_TTL,
  PROJECT_DRAFT_FIELDS,
} from "../dist/drafts.js";

function fixture() {
  const records = new Map();
  const storage = {
    setItem: (k, v) => records.set(k, v),
    getItem: (k) => records.get(k) ?? null,
    removeItem: (k) => records.delete(k),
  };
  let time = 1000;
  return {
    records,
    storage,
    now: () => time,
    advance: (n) => {
      time += n;
    },
  };
}
const fields = () =>
  Object.fromEntries(
    PROJECT_DRAFT_FIELDS.map((id) => [id, `${id}: черновик\nещё строка`]),
  );

test("all form fields survive reopening and a new store instance", () => {
  const f = fixture(),
    original = fields();
  assert.equal(new ProjectDraftStore(f.storage, f.now).save(original), true);
  assert.deepEqual(new ProjectDraftStore(f.storage, f.now).load(), original);
});
test("draft expires after 24 hours and is purged", () => {
  const f = fixture(),
    store = new ProjectDraftStore(f.storage, f.now);
  store.save(fields());
  f.advance(PROJECT_DRAFT_TTL - 1);
  assert.ok(store.load());
  f.advance(1);
  assert.equal(store.load(), null);
  assert.equal(f.records.size, 0);
});
test("another edit renews retention from the last edit", () => {
  const f = fixture(),
    store = new ProjectDraftStore(f.storage, f.now);
  store.save(fields());
  f.advance(PROJECT_DRAFT_TTL - 1);
  store.save({ ...fields(), "form-title": "Новая версия" });
  f.advance(2);
  assert.equal(store.load()["form-title"], "Новая версия");
});
test("successful creation can clear the draft", () => {
  const f = fixture(),
    store = new ProjectDraftStore(f.storage, f.now);
  store.save(fields());
  assert.equal(store.clear(), true);
  assert.equal(new ProjectDraftStore(f.storage, f.now).load(), null);
});
test("corrupt or incompatible local storage does not crash the form", () => {
  const f = fixture(),
    store = new ProjectDraftStore(f.storage, f.now);
  f.storage.setItem(store.key, "broken json");
  assert.equal(store.load(), null);
  f.storage.setItem(
    store.key,
    JSON.stringify({ version: 1, expiresAt: 999999, values: {} }),
  );
  assert.equal(store.load(), null);
});
test("unavailable storage reports failure without throwing", () => {
  const fail = () => {
    throw new Error("blocked");
  };
  const store = new ProjectDraftStore({
    getItem: fail,
    setItem: fail,
    removeItem: fail,
  });
  assert.equal(store.save(fields()), false);
  assert.equal(store.load(), null);
  assert.equal(store.clear(), false);
});
