const TTL = 24 * 60 * 60 * 1000;
const KEY = "deadline.composer-drafts.v1";

export class ComposerDraftStore {
  constructor(storage, now = () => Date.now()) {
    this.now = now;
    this.records = {};
    try {
      this.storage = storage === undefined ? globalThis.localStorage : storage;
      const stored = JSON.parse(this.storage?.getItem(KEY) || "{}");
      this.records = Object.fromEntries(
        Object.entries(stored).filter(
          ([, record]) =>
            record &&
            typeof record.text === "string" &&
            record.text.length <= 8000 &&
            Number.isFinite(record.expiresAt) &&
            record.expiresAt > now(),
        ),
      );
    } catch {}
  }
  load(projectId) {
    const record = this.records[projectId];
    return record?.expiresAt > this.now() ? record.text : "";
  }
  save(projectId, text) {
    if (!projectId) return;
    if (text.trim())
      this.records[projectId] = {
        text: text.slice(0, 8000),
        expiresAt: this.now() + TTL,
      };
    else delete this.records[projectId];
    this.records = Object.fromEntries(
      Object.entries(this.records)
        .filter(([, record]) => record.expiresAt > this.now())
        .sort((a, b) => b[1].expiresAt - a[1].expiresAt)
        .slice(0, 30),
    );
    try {
      this.storage?.setItem(KEY, JSON.stringify(this.records));
    } catch {}
  }
  clear(projectId) {
    this.save(projectId, "");
  }
}
