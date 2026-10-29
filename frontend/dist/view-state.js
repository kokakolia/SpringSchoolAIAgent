// Work changes require acceptance; regenerated explanatory text does not.
export function samePlan(previous, proposal) {
  if (!previous || !proposal) return false;
  const signature = (plan) => [
    plan.buffer_minutes ?? 0,
    plan.tasks.map((task) => [
      ...[
        "id",
        "title",
        "estimate_minutes",
        "priority",
        "decision",
        "done_when",
      ].map((key) => task[key] ?? null),
      [...(task.depends_on || [])].sort(),
      [...(task.constraint_refs || [])].sort((a, b) => a - b),
    ]),
  ];
  return (
    JSON.stringify(signature(previous)) === JSON.stringify(signature(proposal))
  );
}

export function pendingPlan(board) {
  const draft = board?.draft;
  return draft?.validation.valid &&
    samePlan(board.accepted?.proposal, draft.proposal)
    ? null
    : draft;
}

export function displayedPlan(board, view = "plan") {
  return (
    (view === "changes" && pendingPlan(board)) ||
    board?.accepted ||
    pendingPlan(board)
  );
}

export class AcceptedNoticeStore {
  constructor(storage) {
    this.key = "deadline.accepted-notices.v1";
    this.dismissed = {};
    try {
      this.storage = storage === undefined ? globalThis.localStorage : storage;
      const data = JSON.parse(this.storage?.getItem(this.key) || "{}");
      if (data && typeof data === "object" && !Array.isArray(data))
        this.dismissed = data;
    } catch {}
  }
  isDismissed(projectId, planId) {
    return this.dismissed[projectId] === planId;
  }
  dismiss(projectId, planId) {
    this.dismissed[projectId] = planId;
    try {
      this.storage?.setItem(this.key, JSON.stringify(this.dismissed));
    } catch {}
  }
  forget(projectId) {
    delete this.dismissed[projectId];
    try {
      this.storage?.setItem(this.key, JSON.stringify(this.dismissed));
    } catch {}
  }
}

export function proposalIsCurrent(record, project, events) {
  const delta = project.revision - (record.base_revision + 1);
  const newer = events.filter(
    (event) =>
      event.revision > record.base_revision + 1 &&
      event.revision <= project.revision,
  );
  return (
    delta >= 0 &&
    new Set(newer.map((event) => event.revision)).size === delta &&
    newer.every((event) => event.kind === "project_renamed")
  );
}
