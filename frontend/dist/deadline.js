import { createApi } from "./api.js";
import { ComposerDraftStore } from "./composer.js";
import { Demo, exampleState } from "./demo.js";
import { ProjectDraftStore, PROJECT_DRAFT_FIELDS } from "./drafts.js";
import {
  AcceptedNoticeStore,
  displayedPlan,
  pendingPlan,
} from "./view-state.js";
import { patchHTML } from "./dom.js";
const $ = (id) => document.getElementById(id),
  esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
const time = (n) =>
  n === null || n === undefined
    ? "—"
    : n < 60
      ? `${n} мин`
      : `${Math.floor(n / 60)} ч${n % 60 ? " " + (n % 60) + " мин" : ""}`;
const labels = {
  must: "Высокий приоритет",
  should: "Средний приоритет",
  could: "Можно позже",
  keep: "Сохраняем",
  simplify: "Упрощаем",
  defer: "После дедлайна",
};
let mode = "live",
  demo = null,
  board = null,
  projectId = null,
  composerProjectId = null,
  projects = [],
  messages = [],
  view = "plan",
  working = false,
  editing = false,
  newProjectDirty = false,
  draftWarningShown = false,
  outbox = [],
  projectMenuId = null,
  projectMenuAnchor = null,
  projectActionTarget = null,
  toastTimer;
const projectDrafts = new ProjectDraftStore();
const composerDrafts = new ComposerDraftStore();
const acceptedNotices = new AcceptedNoticeStore();
function toast(text) {
  $("toast").textContent = text;
  $("toast").classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.remove("visible"), 5500);
}
const api = createApi();
const path = (suffix) => `/api/projects/${projectId}${suffix}`,
  op = () => ({
    request_id: crypto.randomUUID(),
    base_revision: board.project.revision,
  });
async function refresh({ loadProjects = true, loadMessages = true } = {}) {
  if (mode === "demo") {
    board = demo.workspace();
    projects = demo.list();
    messages = demo.data.messages || [];
    projectId = board?.project.id || null;
  } else {
    if (loadProjects) projects = await api("/api/projects");
    if (!projects.some((p) => p.id === projectId)) {
      let saved;
      try {
        saved = localStorage.getItem("deadline.current");
      } catch {}
      projectId = projects.find((p) => p.id === saved)?.id || projects[0]?.id;
    }
    if (projectId) {
      [board, messages] = await Promise.all([
        api(path("/workspace")),
        loadMessages ? api(path("/messages")) : Promise.resolve(messages),
      ]);
      try {
        localStorage.setItem("deadline.current", projectId);
      } catch {}
    } else {
      board = null;
      messages = [];
    }
  }
  if (board) board.draft = pendingPlan(board);
  render();
}
async function run(
  action,
  label = "Запрос к YandexGPT…",
  onError = null,
  { quiet = false, refreshProjects = !quiet, refreshMessages = !quiet } = {},
) {
  if (working) return false;
  closeProjectMenu();
  working = true;
  document.body.classList.toggle("saving-task", quiet);
  $("busy").hidden = quiet;
  $("busy").querySelector("span").textContent =
    mode === "demo" ? "Демонстрационный сценарий" : label;
  renderDisabled();
  try {
    await action();
    await refresh({
      loadProjects: refreshProjects,
      loadMessages: refreshMessages,
    });
    return true;
  } catch (error) {
    if (onError) onError(error);
    toast(error.message);
    try {
      await refresh({
        loadProjects: refreshProjects,
        loadMessages: refreshMessages,
      });
    } catch {}
    if (!onError && !quiet) {
      const item = document.createElement("div");
      item.className = "message error";
      item.textContent = error.message;
      $("messages").append(item);
    }
    return false;
  } finally {
    working = false;
    $("busy").hidden = true;
    renderDisabled();
    document.body.classList.remove("saving-task");
  }
}
function renderDisabled() {
  document
    .querySelectorAll(
      "#mobile-project-select,#replan,#send,#lose-time,#new-project,#mobile-new,#mobile-memory,#edit-project,#save-project,#compress,#exact-count,#create-first,#create-example,[data-accept],[data-check],[data-pin],[data-project],[data-restore],[data-retry],[data-dismiss],[data-dismiss-status]",
    )
    .forEach((b) => {
      b.disabled = working || b.dataset.unavailable === "true";
    });
  $("project-form")
    .querySelectorAll("input,textarea")
    .forEach((field) => {
      field.disabled = working;
    });
  document
    .querySelectorAll(
      "[data-project-menu],#current-project-menu,#rename-project-dialog input,#rename-project-dialog button,#delete-project-dialog button",
    )
    .forEach((element) => {
      element.disabled = working;
    });
}
function dateText(value) {
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      day: "numeric",
      month: "long",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: board?.project.state.timezone || "Europe/Istanbul",
    }).format(new Date(value));
  } catch {
    return "Дата не задана";
  }
}
function syncComposer() {
  if (composerProjectId === projectId) return;
  if (composerProjectId)
    composerDrafts.save(composerProjectId, $("chat-input").value);
  composerProjectId = projectId;
  $("chat-input").value = projectId ? composerDrafts.load(projectId) : "";
}
function render() {
  syncComposer();
  const p = board?.project,
    s = p?.state,
    display = displayedPlan(board, view),
    validation =
      display?.id === board?.accepted?.id
        ? board?.current_validation
        : display?.validation;
  $("connection").textContent = mode === "demo" ? "Демо · без AI" : "YandexGPT";
  $("connection").classList.toggle("demo", mode === "demo");
  $("animation-prompt").hidden = mode !== "demo";
  patchHTML(
    $("project-list"),
    projects
      .map(
        (p) =>
          `<div class="project-item" data-key="${esc(p.id)}"><button class="project-link ${p.id === projectId ? "active" : ""}" data-project="${esc(p.id)}"><span>↗</span><span>${esc(p.title)}</span></button><button class="project-options" data-project-menu="${esc(p.id)}" aria-label="Действия с проектом «${esc(p.title)}»" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`,
      )
      .join(""),
  );
  $("top-title").textContent = p?.title || "Проект";
  patchHTML(
    $("mobile-project-select"),
    projects
      .map(
        (project) =>
          `<option value="${esc(project.id)}">${esc(project.title)}</option>`,
      )
      .join(""),
  );
  $("mobile-project-select").value = projectId || "";
  $("mobile-project-select").hidden = projects.length < 2;
  $("top-title").classList.toggle("has-project-selector", projects.length >= 2);
  $("project-title").textContent = p?.title || "Нет проектов";
  $("project-goal").textContent = s?.goal || "";
  $("capacity").textContent = time(s?.available_minutes);
  const remaining = s?.deadline ? new Date(s.deadline) - Date.now() : null;
  $("deadline").textContent =
    remaining === null
      ? "—"
      : remaining <= 0
        ? "Истёк"
        : remaining >= 24 * 3600000
          ? `${Math.floor(remaining / 86400000)} д ${Math.floor(remaining / 3600000) % 24} ч`
          : time(Math.ceil(remaining / 60000));
  $("deadline-date").textContent = s?.deadline
    ? dateText(s.deadline)
    : "Дедлайн ещё не задан";
  $("required").textContent = validation
    ? time(validation.required_minutes)
    : "—";
  $("spare").textContent = validation
    ? validation.spare_minutes < 0
      ? `Не хватает ${time(-validation.spare_minutes)}`
      : `Свободно ${time(validation.spare_minutes)}`
    : "";
  $("spare").classList.toggle(
    "negative",
    !!validation && validation.spare_minutes < 0,
  );
  $("empty").hidden = !!p;
  document.querySelector(".project-header").hidden = !p;
  document.querySelector(".stats").hidden = !p;
  document.querySelector(".event-strip").hidden = !p;
  $("workspace-content").hidden = !p;
  $("task-count").textContent =
    display?.proposal.tasks.filter(
      (t) => t.decision !== "defer" && !board.progress[t.id]?.done,
    ).length || "";
  $("change-dot").hidden = !board?.draft;
  $("memory-size").textContent =
    mode === "demo"
      ? ""
      : board
        ? `≈ ${board.context_usage.input_tokens.toLocaleString("ru-RU")} токенов`
        : "";
  $("replan").textContent = display ? "Пересобрать план ↗" : "Составить план ↗";
  $("section-title").textContent = {
    plan: "План",
    changes: "Изменения",
    history: "История",
  }[view];
  document
    .querySelectorAll("[data-view]")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (board) {
    renderStatus();
    renderNext();
    patchHTML(
      $("view-content"),
      view === "plan"
        ? planView(display)
        : view === "changes"
          ? changesView()
          : historyView(),
    );
  } else {
    $("plan-status").innerHTML = "";
    $("next-action").innerHTML = "";
    $("view-content").innerHTML = "";
  }
  renderMessages();
  $("chat-input").disabled = !p;
  for (const id of [
    "edit-project",
    "replan",
    "lose-time",
    "send",
    "memory-button",
  ])
    $(id).dataset.unavailable = String(!p);
  wireRows();
  renderDisabled();
}
function renderMessages() {
  const acknowledged = new Set(messages.map((message) => message.request_id));
  outbox = outbox.filter(
    (turn) => turn.projectId !== projectId || !acknowledged.has(turn.requestId),
  );
  const local = outbox.filter((turn) => turn.projectId === projectId);
  patchHTML(
    $("messages"),
    messages
      .map(
        (m, i) =>
          `<div data-key="${esc(m.request_id || m.id || i)}-${m.role}" class="message ${m.role === "user" ? "user" : "assistant"}">${esc(m.text)}</div>`,
      )
      .join("") +
      local
        .map(
          (turn) =>
            `<div data-key="${turn.requestId}-user" class="message user ${turn.status === "failed" ? "failed" : "pending"}" ${turn.status !== "failed" ? 'aria-busy="true"' : ""}>${esc(turn.text)}<div class="delivery-state">${turn.status === "failed" ? `<span title="${esc(turn.error)}">${turn.syncOnly ? "Не удалось обновить чат" : "Не отправлено"}</span><button data-retry="${turn.requestId}">${turn.syncOnly ? "Обновить" : "Повторить"}</button><button data-dismiss="${turn.requestId}" aria-label="Скрыть сообщение с ошибкой">×</button>` : turn.status === "syncing" ? "Синхронизация…" : "Ожидает ответа…"}</div></div>`,
        )
        .join(""),
  );
  document.querySelector(".chat-intro").hidden =
    messages.length + local.length > 4;
  document.querySelectorAll("[data-retry]").forEach(
    (button) =>
      (button.onclick = () => {
        if (working) return;
        const turn = outbox.find(
          (item) => item.requestId === button.dataset.retry,
        );
        if (!turn) return;
        if (turn.conflict && !turn.syncOnly) {
          turn.requestId = crypto.randomUUID();
          turn.baseRevision = board.project.revision;
        }
        sendTurn(turn);
      }),
  );
  document.querySelectorAll("[data-dismiss]").forEach(
    (button) =>
      (button.onclick = () => {
        if (working) return;
        outbox = outbox.filter(
          (turn) => turn.requestId !== button.dataset.dismiss,
        );
        renderMessages();
      }),
  );
}
function scrollChatToBottom() {
  requestAnimationFrame(() => {
    $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight;
  });
}
function renderStatus() {
  const d = board.draft,
    v = d?.validation || board.current_validation;
  let html = "";
  if (d) {
    const stale = board.draft_stale;
    html = `<div class="status ${stale ? "warning" : v.valid ? "draft" : "error"}"><span>⇄</span><div><strong>${stale ? "План устарел" : v.valid ? "Новый план" : "Нужно уточнить план"}</strong><br>${esc(stale ? "Пересобери план." : d.proposal.summary)}${!stale && !v.valid ? `<ul>${v.errors.map((e) => `<li>${esc(e.message)}</li>`).join("")}</ul>` : ""}</div>${!stale && v.valid ? '<button class="primary" data-accept>Принять план ✓</button>' : ""}</div>`;
  } else if (board.accepted && !v.valid) {
    html = `<div class="status warning"><span>↯</span><div><strong>Нужен пересмотр</strong><br>${esc(v.errors.map((e) => e.message).join(" "))}</div></div>`;
  } else if (
    board.accepted &&
    !acceptedNotices.isDismissed(projectId, board.accepted.id)
  ) {
    html =
      '<div class="status"><span>✓</span><div>План принят</div><button class="dismiss-status" data-dismiss-status aria-label="Скрыть уведомление о принятом плане">×</button></div>';
  }
  patchHTML($("plan-status"), html);
}
function renderNext() {
  const record = displayedPlan(board),
    v = board.accepted ? board.current_validation : record?.validation;
  const task = record?.proposal.tasks.find((t) => t.id === v?.next_task_id);
  patchHTML(
    $("next-action"),
    task &&
      v.valid &&
      (!board.draft_stale || !!board.accepted) &&
      view === "plan"
      ? `<div class="next"><span class="next-marker">↗</span><div><small>${board.accepted ? "СЛЕДУЮЩИЙ ШАГ" : "ПОСЛЕ ПРИНЯТИЯ ПЛАНА"}</small><strong>${esc(task.title)}</strong><p>${esc(task.done_when)}</p></div><span>~ ${time(task.estimate_minutes)}</span></div>`
      : "",
  );
}
function taskRow(t, record) {
  const flags = board.progress[t.id] || {},
    isAccepted = record.id === board.accepted?.id,
    editable = isAccepted && t.decision !== "defer";
  return `<article data-key="${esc(t.id)}" class="task-row ${flags.done ? "done" : ""} ${t.decision === "defer" ? "deferred" : ""}"><button class="check-task ${flags.done ? "done" : ""}" data-check="${esc(t.id)}" data-unavailable="${!editable}" aria-label="${flags.done ? "Вернуть в работу" : "Завершить"}: ${esc(t.title)}" title="${editable ? "Отметить по оценке времени" : "Сначала прими план"}">${flags.done ? "✓" : ""}</button><div class="task-copy"><button class="task-name" data-detail="${esc(t.id)}">${esc(t.title)}</button><p>${esc(t.reason)}</p><div class="task-meta"><span class="pill ${t.priority}">${labels[t.priority]}</span>${t.decision !== "keep" ? `<span class="pill">${labels[t.decision]}</span>` : ""}${t.depends_on.length ? '<span class="pill">↳ Есть зависимости</span>' : ""}${flags.pinned ? '<span class="pill">Закреплена</span>' : ""}</div></div><span class="task-time">${flags.done ? "✓ " + time(flags.spent_minutes) : "~ " + time(t.estimate_minutes)}</span><button class="pin ${flags.pinned ? "pinned" : ""}" data-pin="${esc(t.id)}" data-unavailable="${!editable}" aria-label="${flags.pinned ? "Открепить" : "Закрепить"}: ${esc(t.title)}" title="Закреплённую задачу агент не сможет убрать">♧</button></article>`;
}
function planView(record) {
  if (!record)
    return '<div class="empty"><span>↗</span><p>Составь первый план.</p></div>';
  const tasks = record.proposal.tasks;
  return `<div class="task-list">${tasks
    .filter((t) => t.decision !== "defer")
    .map((t) => taskRow(t, record))
    .join("")}</div>${
    tasks.some((t) => t.decision === "defer")
      ? '<div class="subheading">ОТЛОЖЕНО<span></span></div><div class="task-list">' +
        tasks
          .filter((t) => t.decision === "defer")
          .map((t) => taskRow(t, record))
          .join("") +
        "</div>"
      : ""
  }<details class="assumptions"><summary>Резерв: ${time(record.proposal.buffer_minutes)} · Предположения и риски</summary><ul>${[...record.proposal.assumptions, ...record.proposal.risks].map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>`;
}
function changesView() {
  if (!board.draft)
    return '<div class="empty"><span>⇄</span><p>Пока без изменений.</p></div>';
  const changes = board.diff.filter((c) => c.action !== "unchanged");
  const reserveChanged =
    board.accepted &&
    board.accepted.proposal.buffer_minutes !==
      board.draft.proposal.buffer_minutes;
  const reserve = reserveChanged
    ? `<article class="diff-row"><strong>Резерв</strong><div class="diff-numbers">${time(board.accepted.proposal.buffer_minutes)} → ${time(board.draft.proposal.buffer_minutes)}</div></article>`
    : "";
  const reordered =
    board.accepted &&
    JSON.stringify(board.accepted.proposal.tasks.map((t) => t.id)) !==
      JSON.stringify(board.draft.proposal.tasks.map((t) => t.id));
  return (
    reserve +
      (reordered && !changes.length
        ? '<p class="muted">Изменён порядок задач.</p>'
        : "") +
      changes
        .map((c) => {
          const t = c.after || c.before;
          return `<article class="diff-row"><div class="diff-title"><span class="pill">${{ added: "Добавляем", changed: "Меняем", deferred: "Откладываем", removed: "Исключаем" }[c.action]}</span><button class="task-name" data-detail="${esc(t.id)}">${esc(t.title)}</button></div><div class="diff-numbers">${c.before ? time(c.before.estimate_minutes) + " → " : ""}<span>${c.after?.decision === "defer" ? "после дедлайна" : time(c.after?.estimate_minutes)}</span></div><p>${esc(t.reason)}</p>${t.tradeoff ? `<p><strong>Компромисс:</strong> ${esc(t.tradeoff)}</p>` : ""}</article>`;
        })
        .join("") ||
    '<div class="status"><span>✓</span><div>Состав задач не изменился.</div></div>'
  );
}
function eventDescription(e) {
  if (e.kind === "project_renamed")
    return `Новое название: ${e.payload.title}.`;
  if (e.kind === "time_lost")
    return `Потеряно ${time(e.payload.minutes)}. Осталось ${time(e.payload.remaining_minutes)}.`;
  if (e.kind === "task_changed") {
    const task = board.accepted?.proposal.tasks.find(
      (t) => t.id === e.payload.task_id,
    );
    return `${task?.title || e.payload.task_id}: ${e.payload.done === true ? "завершена" : e.payload.done === false ? "вернулась в работу" : e.payload.pinned ? "закреплена" : "откреплена"}.`;
  }
  return e.kind === "state_updated"
    ? "Уточнены цель, время или обязательные требования."
    : "Пользователь принял предложенный маршрут.";
}
function historyView() {
  return (
    [
      ...board.plans.map((p) => ({
        date: p.created_at,
        title: p.accepted_revision
          ? "Принятая версия плана"
          : "Предложение агента",
        text: p.proposal.summary,
      })),
      ...board.events.map((e) => ({
        date: e.created_at,
        title:
          {
            time_lost: "Изменилось доступное время",
            task_changed: "Изменение задачи",
            state_updated: "Новые условия",
            project_renamed: "Проект переименован",
            plan_accepted: "План принят",
          }[e.kind] || "Событие",
        text: eventDescription(e),
      })),
    ]
      .sort((a, b) => new Date(b.date) - new Date(a.date))
      .map(
        (item) =>
          `<div class="history-item"><small>${esc(dateText(item.date))}</small><p><strong>${esc(item.title)}</strong></p><p>${esc(item.text)}</p></div>`,
      )
      .join("") || '<p class="muted">История пуста.</p>'
  );
}
function wireRows() {
  document.querySelectorAll("[data-project-menu]").forEach((button) => {
    button.onclick = () => openProjectMenu(button.dataset.projectMenu, button);
  });
  document.querySelectorAll("[data-dismiss-status]").forEach((button) => {
    button.onclick = () => {
      acceptedNotices.dismiss(projectId, board.accepted.id);
      renderStatus();
    };
  });
  document
    .querySelectorAll("[data-project]")
    .forEach((b) => (b.onclick = () => selectProject(b.dataset.project)));
  document
    .querySelectorAll("[data-detail]")
    .forEach((b) => (b.onclick = () => showTask(b.dataset.detail)));
  document.querySelectorAll("[data-check]").forEach(
    (b) =>
      (b.onclick = () =>
        changeTask(b.dataset.check, {
          done: !board.progress[b.dataset.check]?.done,
        })),
  );
  document.querySelectorAll("[data-pin]").forEach(
    (b) =>
      (b.onclick = () =>
        changeTask(b.dataset.pin, {
          pinned: !board.progress[b.dataset.pin]?.pinned,
        })),
  );
  document.querySelectorAll("[data-accept]").forEach(
    (b) =>
      (b.onclick = () =>
        run(async () => {
          if (mode === "demo") demo.accept();
          else await api(path(`/plans/${board.draft.id}/accept`), op());
          view = "plan";
          toast("План принят.");
        }, "Сохраняю принятую версию…")),
  );
}
function selectProject(next) {
  if (working || next === projectId) return;
  const previousId = projectId,
    previousView = view;
  return run(
    async () => {
      projectId = next;
      view = "plan";
    },
    "Загрузка проекта…",
    () => {
      projectId = previousId;
      view = previousView;
      render();
    },
  );
}
$("mobile-project-select").onchange = (event) =>
  selectProject(event.target.value);
function showTask(id) {
  const record = displayedPlan(board, view),
    t = record?.proposal.tasks.find((t) => t.id === id);
  if (!t) return;
  $("task-detail-title").textContent = t.title;
  const names = t.depends_on.map(
    (id) => record.proposal.tasks.find((t) => t.id === id)?.title || id,
  );
  let html = [
    ["ПОЧЕМУ", t.reason],
    ["ГОТОВО, КОГДА", t.done_when],
    ["ОЦЕНКА", time(t.estimate_minutes)],
    ["КОМПРОМИСС", t.tradeoff],
    ["ЗАВИСИМОСТИ", names.join("; ")],
  ]
    .filter(([, text]) => text)
    .map(
      ([title, text]) =>
        `<div class="detail-block"><h3>${title}</h3><p>${esc(text)}</p></div>`,
    )
    .join("");
  const schedule = record.validation.schedule?.find((s) => s.task_id === id);
  if (schedule?.segments?.length)
    html += `<div class="detail-block"><h3>РАБОЧИЕ ИНТЕРВАЛЫ</h3><p>${schedule.segments.map((s) => esc(dateText(s.start) + " — " + dateText(s.end))).join("<br>")}</p></div>`;
  if (
    record.id === board.accepted?.id &&
    !board.progress[id]?.done &&
    t.decision !== "defer"
  )
    html += `<div class="detail-block"><label for="actual-minutes">Фактически потрачено минут</label><input id="actual-minutes" type="number" min="0" max="525600" value="${t.estimate_minutes}"><div class="dialog-actions"><button id="complete-with-time" class="primary">Завершить ✓</button></div></div>`;
  $("task-detail").innerHTML = html;
  $("task-dialog").showModal();
  if ($("complete-with-time"))
    $("complete-with-time").onclick = () => {
      const n = Number($("actual-minutes").value);
      if (
        $("actual-minutes").value === "" ||
        !Number.isInteger(n) ||
        n < 0 ||
        n > 525600
      ) {
        toast("Укажи целое число минут от 0 до 525600");
        return;
      }
      $("task-dialog").close();
      changeTask(id, { done: true, actual_minutes: n });
    };
}
function changeTask(id, patch) {
  return run(
    async () => {
      if (mode === "demo") demo.task(id, patch);
      else
        await api(
          path(`/tasks/${encodeURIComponent(id)}`),
          { ...op(), ...patch },
          "PATCH",
        );
    },
    "Сохраняю изменение…",
    null,
    { quiet: true },
  );
}
async function propose(text, { fromComposer = false } = {}) {
  if (!board || working) return;
  if (fromComposer) {
    $("chat-input").value = "";
    composerDrafts.clear(projectId);
  }
  if (text.trim() === "/compress") {
    await compress();
    return;
  }
  const turn = {
    projectId,
    text,
    requestId: crypto.randomUUID(),
    baseRevision: board.project.revision,
    status: "pending",
  };
  outbox.push(turn);
  await sendTurn(turn);
}
async function sendTurn(turn) {
  if (working) return;
  turn.status = "pending";
  renderMessages();
  scrollChatToBottom();
  let completed = !!turn.syncOnly;
  await run(
    async () => {
      if (!turn.syncOnly) {
        let hasChanges;
        if (mode === "demo") {
          demo.propose(turn.text);
          hasChanges = !!demo.workspace().draft;
          outbox = outbox.filter((item) => item !== turn);
        } else {
          const result = await api(
            `/api/projects/${turn.projectId}/plans/propose`,
            {
              request_id: turn.requestId,
              base_revision: turn.baseRevision,
              text: turn.text,
            },
          );
          hasChanges = !!pendingPlan({ ...board, draft: result.plan });
        }
        completed = true;
        turn.status = "syncing";
        view = board.accepted && hasChanges ? "changes" : "plan";
      }
    },
    "YandexGPT готовит новое предложение…",
    (error) => {
      turn.status = "failed";
      turn.error = error.message;
      turn.conflict = error.status === 409 && error.code !== "operation_busy";
      turn.syncOnly = completed;
    },
  );
  renderMessages();
  renderDisabled();
  scrollChatToBottom();
}
function focusChat(text = "") {
  if (text) {
    $("chat-input").value = text;
    composerDrafts.save(projectId, text);
  }
  $("chat").classList.add("open");
  $("chat-input").focus();
}
document.querySelectorAll("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      view = b.dataset.view;
      render();
    }),
);
$("replan").onclick = () =>
  propose(
    "Составь или пересобери реалистичный план с учётом актуальных условий, событий, моих возражений и уже выполненных задач. Объясни компромиссы.",
  );
$("animation-prompt").onclick = () =>
  focusChat(
    "Анимации важны для показа. Сохрани их и предложи, что сократить вместо них.",
  );
$("why-prompt").onclick = () =>
  focusChat(
    "Объясни, какие задачи ты предлагаешь отложить и почему это лучше для результата.",
  );
$("mobile-chat").onclick = () => focusChat();
$("close-chat").onclick = () => {
  if (matchMedia("(max-width:1000px)").matches) {
    $("chat").classList.remove("open");
    $("mobile-chat").focus();
  } else $("chat-input").focus();
};
$("chat-input").addEventListener("input", () => {
  composerDrafts.save(composerProjectId, $("chat-input").value);
});
$("chat-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("chat-input").value.trim();
  if (text) propose(text, { fromComposer: true });
};
$("chat-input").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
};
$("lose-time").onclick = () =>
  run(async () => {
    if (mode === "demo") {
      demo.loseTime();
      demo.propose("Потерял два часа времени. Пересобери план.");
    } else {
      await api(path("/events/time-lost"), { ...op(), minutes: 120 });
      await refresh();
      await api(path("/plans/propose"), {
        ...op(),
        text: "Я потерял два часа рабочего времени. Учти новое доступное время и предложи пересобранный план с объяснением изменений.",
      });
    }
    view = "changes";
  }, "Учитываю потерю времени и запрашиваю новый план…");
function closeProjectMenu(restoreFocus = false) {
  $("project-menu").hidden = true;
  projectMenuAnchor?.setAttribute("aria-expanded", "false");
  if (restoreFocus && projectMenuAnchor?.isConnected) projectMenuAnchor.focus();
  projectMenuId = null;
  projectMenuAnchor = null;
}
function openProjectMenu(id, anchor) {
  if (working || !id) return;
  if (projectMenuId === id && !$("project-menu").hidden) {
    closeProjectMenu(true);
    return;
  }
  closeProjectMenu();
  projectMenuId = id;
  projectMenuAnchor = anchor;
  const menu = $("project-menu"),
    rect = anchor.getBoundingClientRect();
  menu.hidden = false;
  menu.style.left = `${Math.max(8, Math.min(rect.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(rect.bottom + 5, innerHeight - menu.offsetHeight - 8))}px`;
  anchor.setAttribute("aria-expanded", "true");
  $("rename-project-action").focus();
}
function openProjectAction(action) {
  if (working) return;
  const target =
    projectMenuId === projectId
      ? board?.project
      : projects.find((p) => p.id === projectMenuId);
  if (!target) return;
  projectActionTarget = { ...target };
  closeProjectMenu();
  if (action === "rename") {
    $("rename-project-name").value = target.title;
    $("rename-project-error").textContent = "";
    $("rename-project-dialog").showModal();
    $("rename-project-name").focus();
    $("rename-project-name").select();
  } else {
    $("delete-project-name").textContent = target.title;
    $("delete-project-error").textContent = "";
    $("delete-project-dialog").showModal();
    $("delete-project-dialog")
      .querySelector('[data-close="delete-project-dialog"]')
      .focus();
  }
}
function updateActionRevision(dialogId) {
  const fresh =
    projectActionTarget?.id === projectId
      ? board?.project
      : projects.find((p) => p.id === projectActionTarget?.id);
  if (!fresh) {
    $(dialogId).close();
    return;
  }
  projectActionTarget = { ...fresh };
  $("delete-project-name").textContent = fresh.title;
}
$("current-project-menu").onclick = (event) =>
  openProjectMenu(projectId, event.currentTarget);
$("rename-project-action").onclick = () => openProjectAction("rename");
$("delete-project-action").onclick = () => openProjectAction("delete");
document.addEventListener("click", (event) => {
  if (
    !$("project-menu").contains(event.target) &&
    !event.target.closest?.("[data-project-menu],#current-project-menu")
  )
    closeProjectMenu();
});
window.addEventListener("resize", () => closeProjectMenu());
document.addEventListener("scroll", () => closeProjectMenu(), true);
$("project-menu").onkeydown = (event) => {
  if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const items = [...$("project-menu").querySelectorAll("button")];
    const index = items.indexOf(document.activeElement);
    items[
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) %
            items.length
    ].focus();
  } else if (event.key === "Tab") closeProjectMenu();
};
$("rename-project-form").onsubmit = async (event) => {
  event.preventDefault();
  if (working || !projectActionTarget) return;
  const title = $("rename-project-name").value.trim(),
    target = { ...projectActionTarget };
  if (!title) {
    $("rename-project-error").textContent = "Укажи название проекта.";
    return;
  }
  $("rename-project-error").textContent = "";
  const saved = await run(
    async () => {
      if (mode === "demo") demo.renameProject(title);
      else
        await api(
          `/api/projects/${target.id}`,
          { base_revision: target.revision, title },
          "PATCH",
        );
      $("rename-project-dialog").close();
      toast("Название сохранено.");
    },
    "Сохраняю название…",
    (error) => {
      $("rename-project-error").textContent = error.message;
    },
    { quiet: true, refreshProjects: true },
  );
  if (!saved) updateActionRevision("rename-project-dialog");
};
$("confirm-delete-project").onclick = async () => {
  if (working || !projectActionTarget) return;
  const target = { ...projectActionTarget };
  $("delete-project-error").textContent = "";
  const removed = await run(
    async () => {
      if (mode === "demo") demo.deleteProject();
      else {
        try {
          await api(
            `/api/projects/${target.id}`,
            { base_revision: target.revision },
            "DELETE",
          );
        } catch (error) {
          if (error.status !== 404) throw error;
        }
      }
      outbox = outbox.filter((turn) => turn.projectId !== target.id);
      acceptedNotices.forget(target.id);
      composerDrafts.clear(target.id);
      if (projectId === target.id) {
        projectId = null;
        composerProjectId = null;
        board = null;
        messages = [];
        view = "plan";
        $("chat-input").value = "";
        try {
          localStorage.removeItem("deadline.current");
        } catch {}
      }
      $("delete-project-dialog").close();
      toast("Проект удалён.");
    },
    "Удаляю проект…",
    (error) => {
      $("delete-project-error").textContent = error.message;
    },
    { quiet: true, refreshProjects: true, refreshMessages: true },
  );
  if (!removed) updateActionRevision("delete-project-dialog");
};
function openProject(isEdit) {
  if (working) return;
  if (mode === "demo") {
    toast(
      "Редактирование проектов доступно в локальном приложении. Здесь — готовый демо-сценарий.",
    );
    return;
  }
  editing = isEdit;
  const p = editing ? board.project : null,
    s = p?.state || exampleState();
  $("project-dialog-title").textContent = editing
    ? "Условия проекта"
    : "Новый проект";
  $("form-title").value = p?.title || "";
  $("form-goal").value = p ? s.goal : "";
  const offset = s.deadline?.match(/([+-]\d\d:\d\d)$/)?.[1] || "+03:00";
  const utc = new Date(s.deadline || Date.now() + 48 * 3600000);
  const sign = offset[0] === "-" ? -1 : 1;
  const [h, m] = offset.slice(1).split(":").map(Number);
  $("form-deadline").value = new Date(
    utc.getTime() + sign * (h * 60 + m) * 60000,
  )
    .toISOString()
    .slice(0, 16);
  $("form-offset").value = offset;
  $("form-timezone").value = s.timezone;
  $("form-minutes").value = s.available_minutes ?? 360;
  $("form-success").value = p ? s.success_criteria.join("\n") : "";
  $("form-constraints").value = p ? s.constraints.join("\n") : "";
  $("form-windows").value = (s.work_windows || [])
    .map((w) => `${w.start} / ${w.end}`)
    .join("\n");
  $("form-error").textContent = "";
  newProjectDirty = false;
  if (!editing) {
    const draft = projectDrafts.load();
    if (draft) {
      for (const id of PROJECT_DRAFT_FIELDS) $(id).value = draft[id];
      newProjectDirty = true;
      $("project-dialog-title").textContent = "Новый проект · черновик";
    }
  }
  $("project-dialog").showModal();
}
function saveProjectDraft() {
  if (editing || !newProjectDirty) return;
  const values = Object.fromEntries(
    PROJECT_DRAFT_FIELDS.map((id) => [id, $(id).value]),
  );
  if (!projectDrafts.save(values) && !draftWarningShown) {
    draftWarningShown = true;
    toast("Не удалось сохранить черновик в этом браузере.");
  }
}
$("project-form").addEventListener("input", () => {
  if (!editing) {
    newProjectDirty = true;
    saveProjectDraft();
  }
});
$("project-form").addEventListener("change", () => {
  if (!editing) {
    newProjectDirty = true;
    saveProjectDraft();
  }
});
$("project-dialog").addEventListener("close", saveProjectDraft);
window.addEventListener("pagehide", () => {
  composerDrafts.save(composerProjectId, $("chat-input").value);
  if ($("project-dialog").open) saveProjectDraft();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && $("project-dialog").open)
    saveProjectDraft();
});
$("new-project").onclick = $("create-first").onclick = () => openProject(false);
$("mobile-new").onclick = () => openProject(false);
$("edit-project").onclick = () => openProject(true);
$("create-example").onclick = () =>
  run(async () => {
    if (mode === "demo") {
      demo.restoreExample();
      projectId = "demo";
      view = "plan";
      return;
    }
    const p = await api("/api/projects", {
      title: "Пример · защита AI-проекта",
      state: exampleState(),
    });
    projectId = p.id;
  }, "Создаю пример проекта…");
$("project-form").onsubmit = (e) => {
  e.preventDefault();
  if (working) return;
  $("form-error").textContent = "";
  run(
    async () => {
      const lines = (id) =>
        $(id)
          .value.split("\n")
          .map((s) => s.trim())
          .filter(Boolean);
      const windows = lines("form-windows").map((line) => {
        const parts = line.split(" / ");
        if (parts.length !== 2)
          throw Error("Интервалы: начало / конец, каждый с часовым поясом");
        return { start: parts[0], end: parts[1] };
      });
      const deadline = $("form-deadline").value + $("form-offset").value;
      if (Number.isNaN(new Date(deadline).getTime()))
        throw Error("Проверь дату и смещение часового пояса");
      const state = {
        goal: $("form-goal").value.trim(),
        success_criteria: lines("form-success"),
        constraints: lines("form-constraints"),
        available_minutes: Number($("form-minutes").value),
        deadline,
        timezone: $("form-timezone").value.trim(),
        work_windows: windows,
      };
      if (editing)
        await api(
          path(""),
          {
            base_revision: board.project.revision,
            title: document.getElementById("form-title").value.trim(),
            state,
          },
          "PATCH",
        );
      else {
        const p = await api("/api/projects", {
          title: $("form-title").value.trim(),
          state,
        });
        projectId = p.id;
        newProjectDirty = false;
        projectDrafts.clear();
      }
      $("project-dialog").close();
      toast("Условия сохранены.");
    },
    "Сохраняю условия…",
    (error) => {
      $("form-error").textContent = error.message;
    },
  );
};
document
  .querySelectorAll("[data-close]")
  .forEach((b) => (b.onclick = () => $(b.dataset.close).close()));
document.querySelectorAll("dialog").forEach(
  (d) =>
    (d.onclick = (e) => {
      if (e.target === d) {
        const r = d.getBoundingClientRect();
        if (
          e.clientX < r.left ||
          e.clientX > r.right ||
          e.clientY < r.top ||
          e.clientY > r.bottom
        )
          d.close();
      }
    }),
);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("project-menu").hidden) {
    e.preventDefault();
    closeProjectMenu(true);
    return;
  }
  if (e.key === "Escape") $("chat").classList.remove("open");
});
async function showMemory(exact = false) {
  if (mode === "demo") {
    toast(
      "Сжатие и версии памяти работают в локальном приложении с настоящим YandexGPT.",
    );
    return;
  }
  if (!board) return;
  const [context, versions] = await Promise.all([
    api(path("/context") + (exact ? "?exact=true" : "")),
    api(path("/context/versions")),
  ]);
  const u = context.usage;
  $("memory-details").innerHTML =
    `<div class="memory-stat">${u.count_method === "estimated_utf8" ? "≈ " : ""}${u.input_tokens.toLocaleString("ru-RU")} токенов</div><p class="muted">${exact ? "Токенизатор Yandex" : "Локальная оценка"} · резерв ${u.response_reserve_tokens} · бюджет ${u.configured_budget_tokens}</p>`;
  $("memory-versions").innerHTML =
    versions
      .slice()
      .reverse()
      .map(
        (v) =>
          `<div class="memory-version"><strong>${{ compressed: "После сжатия", before_compression: "До сжатия", restored: "Восстановленная память" }[v.kind] || v.kind}</strong><p>${esc(dateText(v.created_at))} · ≈ ${v.estimated_tokens} токенов</p>${v.summary ? `<details><summary>Краткая память</summary><pre>${esc(JSON.stringify(v.summary, null, 2))}</pre></details>` : ""}<button class="quiet-button" data-restore="${esc(v.id)}">Восстановить ↗</button></div>`,
      )
      .join("") || '<p class="muted">Сохранённых версий пока нет.</p>';
  document.querySelectorAll("[data-restore]").forEach(
    (b) =>
      (b.onclick = () =>
        run(async () => {
          await api(
            path(`/context/versions/${b.dataset.restore}/restore`),
            op(),
          );
          await refresh();
          await showMemory();
          toast("Версия восстановлена.");
        }, "Восстанавливаю контекст…")),
  );
  if (!$("memory-dialog").open) $("memory-dialog").showModal();
  renderDisabled();
}
$("memory-button").onclick = () =>
  run(() => showMemory(), "Читаю память проекта…");
$("mobile-memory").onclick = () =>
  run(() => showMemory(), "Читаю память проекта…");
$("exact-count").onclick = () =>
  run(() => showMemory(true), "Запрашиваю точный размер…");
async function compress() {
  if (mode === "demo") {
    toast("Сжатие доступно в локальном приложении.");
    return;
  }
  await run(async () => {
    await api(path("/context/compress"), op());
    await refresh();
    await showMemory();
    toast("Сохранены версии до и после сжатия.");
  }, "YandexGPT сжимает контекст…");
}
$("compress").onclick = compress;
try {
  const response =
    new URLSearchParams(location.search).get("demo") === "1"
      ? new Response(null, { status: 404 })
      : await fetch("/api/runtime");
  if (
    response.status === 404 ||
    (response.headers.get("content-type") || "").includes("text/html")
  ) {
    mode = "demo";
    demo = new Demo();
    projectId = "demo";
  } else {
    if (!response.ok) throw Error("Сервер приложения недоступен");
    const runtime = await response.json();
    if (runtime.service !== "deadline")
      throw Error("Неожиданный сервер приложения");
  }
  await refresh();
} catch (error) {
  $("project-title").textContent = "Не удалось загрузить пространство";
  $("project-goal").textContent =
    "Проверь, запущен ли сервер, и обнови страницу.";
  $("connection").textContent = "Нет подключения";
  toast(error.message);
}
