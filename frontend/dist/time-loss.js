const PREFIX = "deadline.time-loss.v1:";

export class TimeLossStore {
  constructor(storage) {
    this.memory = new Map();
    try {
      this.storage = storage === undefined ? globalThis.localStorage : storage;
    } catch {}
  }
  load(projectId) {
    if (this.memory.has(projectId)) return this.memory.get(projectId);
    try {
      const value = JSON.parse(
        this.storage?.getItem(PREFIX + projectId) || "null",
      );
      if (
        value &&
        ["event", "plan", "refresh"].includes(value.phase) &&
        typeof value.eventRequestId === "string" &&
        typeof value.planRequestId === "string" &&
        Number.isInteger(value.minutes) &&
        value.minutes > 0 &&
        value.minutes <= 525600
      ) {
        this.memory.set(projectId, value);
        return value;
      }
    } catch {}
    return null;
  }
  begin(projectId, minutes, uuid = () => crypto.randomUUID()) {
    const existing = this.load(projectId);
    if (existing) return existing;
    const operation = {
      phase: "event",
      minutes,
      eventRequestId: uuid(),
      planRequestId: uuid(),
    };
    if (!this.save(projectId, operation)) {
      this.memory.delete(projectId);
      throw Error(
        "Не удалось сохранить событие для повтора. Проверь доступ к хранилищу браузера.",
      );
    }
    return operation;
  }
  save(projectId, operation) {
    this.memory.set(projectId, operation);
    try {
      if (!this.storage) return false;
      this.storage.setItem(PREFIX + projectId, JSON.stringify(operation));
      return true;
    } catch {
      return false;
    }
  }
  clear(projectId) {
    this.memory.delete(projectId);
    try {
      this.storage?.removeItem(PREFIX + projectId);
    } catch {}
  }
}

// The persisted phase is advanced only after acknowledgment; IDs survive lost responses.
export async function resumeTimeLoss(
  store,
  projectId,
  { getRevision, recordLoss, proposePlan },
) {
  const operation = store.load(projectId);
  if (!operation) throw Error("Нет события для повтора");
  if (operation.phase === "event") {
    await recordLoss({
      request_id: operation.eventRequestId,
      base_revision: await getRevision(),
      minutes: operation.minutes,
    });
    operation.phase = "plan";
    store.save(projectId, operation);
  }
  if (operation.phase === "plan") {
    await proposePlan({
      request_id: operation.planRequestId,
      base_revision: await getRevision(),
      text: `Я потерял ${operation.minutes} минут рабочего времени. Учти новое доступное время и предложи пересобранный план с объяснением изменений.`,
    });
    operation.phase = "refresh";
    store.save(projectId, operation);
  }
  // Clear only after the UI has successfully refreshed the saved workspace.
}
