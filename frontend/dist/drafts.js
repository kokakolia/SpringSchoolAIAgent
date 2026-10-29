export const PROJECT_DRAFT_TTL = 24 * 60 * 60 * 1000;
export const PROJECT_DRAFT_FIELDS = [
  "form-title",
  "form-goal",
  "form-deadline",
  "form-timezone",
  "form-minutes",
  "form-offset",
  "form-success",
  "form-constraints",
  "form-windows",
];

export class ProjectDraftStore {
  constructor(storage, now = () => Date.now()) {
    try {
      this.storage = storage === undefined ? globalThis.localStorage : storage;
    } catch {
      this.storage = null;
    }
    this.now = now;
    this.key = "deadline.new-project-draft.v1";
  }
  save(values) {
    const fields = Object.fromEntries(
      PROJECT_DRAFT_FIELDS.map((id) => [id, String(values[id] ?? "")]),
    );
    try {
      this.storage.setItem(
        this.key,
        JSON.stringify({
          version: 1,
          expiresAt: this.now() + PROJECT_DRAFT_TTL,
          values: fields,
        }),
      );
      return true;
    } catch {
      return false;
    }
  }
  load() {
    try {
      const record = JSON.parse(this.storage.getItem(this.key));
      if (!record) return null;
      if (
        record.version !== 1 ||
        !Number.isFinite(record.expiresAt) ||
        record.expiresAt <= this.now() ||
        !record.values ||
        PROJECT_DRAFT_FIELDS.some((id) => typeof record.values[id] !== "string")
      ) {
        this.clear();
        return null;
      }
      return Object.fromEntries(
        PROJECT_DRAFT_FIELDS.map((id) => [id, record.values[id]]),
      );
    } catch {
      return null;
    }
  }
  clear() {
    try {
      this.storage.removeItem(this.key);
      return true;
    } catch {
      return false;
    }
  }
}
