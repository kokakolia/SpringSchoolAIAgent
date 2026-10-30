import { pendingPlan, proposalIsCurrent, samePlan } from "./view-state.js";
// Scripted, browser-local showcase. This module never claims to call an AI model.
const key = "deadline.showcase.v2";
const copy = (value) => structuredClone(value);
export function exampleState() {
  return {
    goal: "Показать работающий AI-проект на защите",
    completed: false,
    success_criteria: [
      "Пользователь проходит основной сценарий от ввода до сохранённого результата",
    ],
    constraints: ["Подготовить презентацию для защиты"],
    available_minutes: 360,
    deadline: new Date(Date.now() + 48 * 3600000).toISOString(),
    timezone: "Europe/Istanbul",
    work_windows: [],
  };
}
function initialPlan() {
  const task = (
    id,
    title,
    estimate_minutes,
    priority,
    depends_on,
    done_when,
    reason,
    constraint_refs = [],
  ) => ({
    id,
    title,
    estimate_minutes,
    priority,
    depends_on,
    done_when,
    reason,
    constraint_refs,
    decision: "keep",
    tradeoff: "",
  });
  return {
    summary:
      "Сохраним рабочий сценарий, презентацию и проверку запуска. На всё — 5 часов 20 минут вместе с резервом.",
    buffer_minutes: 30,
    assumptions: [
      "Демонстрационные оценки. Их нужно уточнить по реальному объёму работ.",
    ],
    questions: [],
    risks: ["Проверка сохранения может обнаружить дополнительные ошибки."],
    tasks: [
      task(
        "api",
        "Проверить сохранение через API",
        40,
        "must",
        [],
        "Созданная задача остаётся после перезапуска.",
        "Это основа показа: без сохранения основной сценарий не завершён.",
      ),
      task(
        "ui",
        "Довести основной экран до готовности",
        80,
        "must",
        ["api"],
        "Можно ввести запрос и увидеть сохранённый результат.",
        "Готовый экран превращает отдельные функции в понятную демонстрацию.",
      ),
      task(
        "slides",
        "Подготовить историю для защиты",
        45,
        "must",
        [],
        "Пять слайдов: проблема, решение, архитектура, демонстрация, выводы.",
        "Презентация — обязательное требование защиты.",
        [1],
      ),
      task(
        "tests",
        "Пройти демонстрацию от начала до конца",
        40,
        "must",
        ["ui"],
        "Полный сценарий проходит без ручных исправлений.",
        "Проверка снижает риск сбоя перед аудиторией.",
      ),
      task(
        "animation",
        "Добавить выразительные переходы",
        60,
        "could",
        ["ui"],
        "Переходы состояний работают и не мешают основному сценарию.",
        "Анимация делает работу агента понятнее и усиливает показ.",
      ),
      task(
        "readme",
        "Дополнить README примерами",
        25,
        "should",
        [],
        "В README есть примеры запросов и ответов.",
        "Примеры помогут разобраться в проекте после защиты.",
      ),
    ],
  };
}
function validate(proposal, state, progress) {
  const tasks = proposal.tasks.filter(
      (t) => t.decision !== "defer" && !progress[t.id]?.done,
    ),
    work = tasks.reduce((s, t) => s + t.estimate_minutes, 0),
    required = work + (state.completed ? 0 : proposal.buffer_minutes);
  const missingWork = !tasks.length && !state.completed;
  return {
    valid: !missingWork && required <= state.available_minutes,
    errors: missingWork
      ? [
          {
            message:
              "Подтверди завершение проекта или добавь работу для достижения цели.",
          },
        ]
      : required > state.available_minutes
        ? [
            {
              message: `План требует ${required} мин., доступно ${state.available_minutes} мин.`,
            },
          ]
        : [],
    work_minutes: work,
    buffer_minutes: proposal.buffer_minutes,
    required_minutes: required,
    capacity_minutes: state.available_minutes,
    spare_minutes: state.available_minutes - required,
    schedule: tasks.map((t) => ({ task_id: t.id })),
    schedule_kind: "sequence",
    next_task_id: tasks.find((t) =>
      t.depends_on.every((d) => progress[d]?.done),
    )?.id,
  };
}
function seed() {
  const state = exampleState(),
    proposal = initialPlan(),
    created_at = new Date().toISOString();
  return {
    project: { id: "demo", title: "Защита AI-проекта", state, revision: 2 },
    accepted: {
      id: "demo-plan-1",
      proposal,
      validation: validate(proposal, state, {}),
      base_revision: 0,
      accepted_revision: 2,
      created_at,
    },
    draft: null,
    progress: {},
    plans: [],
    events: [
      {
        id: "start",
        kind: "plan_accepted",
        payload: { plan_id: "demo-plan-1" },
        revision: 2,
        created_at,
      },
    ],
    messages: [
      {
        role: "assistant",
        text: "План готов.",
      },
    ],
    wantAnimation: false,
  };
}
export class Demo {
  constructor() {
    try {
      this.data = JSON.parse(localStorage.getItem(key));
      if (this.data?.deleted === true) return;
      if (!this.data?.project?.state || !this.data?.accepted) throw Error();
    } catch {
      this.data = seed();
    }
    this.data.messages = this.data.messages.map((message) =>
      message.text ===
      "Вот первый маршрут к защите. Можно оспорить любую задачу — её причину увидишь по нажатию на карточку."
        ? { ...message, text: "План готов." }
        : message,
    );
    if (!this.data.plans.length) this.data.plans = [copy(this.data.accepted)];
    if (!this.data.timeBudget) {
      const charged = Object.values(this.data.progress).reduce(
        (sum, flags) =>
          sum +
          (flags.done ? (flags.debited_minutes ?? flags.spent_minutes) : 0),
        0,
      );
      const lost = this.data.events
        .filter((e) => e.kind === "time_lost")
        .reduce((sum, e) => sum + e.payload.minutes, 0);
      this.data.timeBudget = {
        allocated: this.data.project.state.available_minutes + lost + charged,
        lost,
        charged,
      };
    }
  }
  save() {
    try {
      localStorage.setItem(key, JSON.stringify(this.data));
    } catch {}
  }
  list() {
    if (this.data.deleted) return [];
    return [this.data.project];
  }
  workspace() {
    if (this.data.deleted) return null;
    const d = copy(this.data);
    d.accepted.validation = validate(
      d.accepted.proposal,
      d.project.state,
      d.progress,
    );
    d.current_validation = d.accepted.validation;
    d.draft_stale =
      !!d.draft && !proposalIsCurrent(d.draft, d.project, d.events);
    d.diff = d.draft
      ? d.draft.proposal.tasks.map((t) => {
          const before = d.accepted.proposal.tasks.find((o) => o.id === t.id);
          return {
            task_id: t.id,
            before,
            after: t,
            action: !before
              ? "added"
              : t.decision === "defer" && before.decision !== "defer"
                ? "deferred"
                : JSON.stringify(before) === JSON.stringify(t)
                  ? "unchanged"
                  : "changed",
          };
        })
      : [];
    d.context_usage = { input_tokens: 0, count_method: "demo" };
    d.draft = pendingPlan(d);
    if (!d.draft) {
      d.diff = [];
      d.draft_stale = false;
    }
    return d;
  }
  event(kind, payload, requestId = crypto.randomUUID()) {
    this.data.project.revision++;
    this.data.events.unshift({
      id: crypto.randomUUID(),
      request_id: requestId,
      kind,
      payload,
      revision: this.data.project.revision,
      created_at: new Date().toISOString(),
    });
  }
  updateBudget() {
    const budget = this.data.timeBudget;
    this.data.project.state.available_minutes = Math.max(
      0,
      budget.allocated - budget.lost - budget.charged,
    );
  }
  loseTime(minutes = 120, requestId = crypto.randomUUID()) {
    if (this.data.events.some((event) => event.request_id === requestId))
      return;
    this.data.timeBudget.lost += minutes;
    this.updateBudget();
    this.event(
      "time_lost",
      { minutes, remaining_minutes: this.data.project.state.available_minutes },
      requestId,
    );
    this.save();
  }
  propose(text) {
    const d = this.data;
    d.messages.push({ role: "user", text });
    if (!/анимац|переход|пересоб|потер|план|отлож|сократ|врем/i.test(text)) {
      d.messages.push({
        role: "assistant",
        text: "Это демонстрация готового сценария. Попробуй «−2 часа времени» или «Анимации важны для показа». Произвольные запросы обрабатывает YandexGPT в локальном приложении.",
      });
      this.save();
      return;
    }
    if (/анимац|переход/i.test(text)) d.wantAnimation = true;
    const p = initialPlan();
    if (d.project.state.available_minutes < 320) {
      for (const t of p.tasks) {
        if (d.progress[t.id]?.done || d.progress[t.id]?.pinned) continue;
        if (t.id === "readme" || (t.id === "animation" && !d.wantAnimation)) {
          t.decision = "defer";
          t.reason =
            t.id === "readme"
              ? "Примеры в документации можно дописать после защиты."
              : "При сокращении времени основной сценарий важнее полировки переходов.";
          t.tradeoff =
            t.id === "readme"
              ? "Документация будет менее подробной; время остаётся для показа."
              : "Демонстрация станет менее выразительной; сохраним рабочий сценарий.";
        }
        if (["ui", "slides", "tests"].includes(t.id)) {
          t.decision = "simplify";
          t.estimate_minutes = { ui: 60, slides: 35, tests: 35 }[t.id];
          t.reason = "Сосредоточимся на одном демонстрационном сценарии.";
          t.tradeoff =
            "Сократим дополнительные варианты, сохранив проверяемый результат.";
        }
      }
      p.buffer_minutes = d.wantAnimation ? 10 : 20;
      p.summary = d.wantAnimation
        ? "Сохраняем анимации: они важны для твоего показа. Сократим дополнительные варианты интерфейса и объём слайдов; примеры README перенесём."
        : "Времени стало меньше. Перенесём анимации и примеры README, упростим второстепенные варианты. Основной сценарий и проверка остаются.";
    } else
      p.summary =
        "План помещается в доступное время. Сохраняем основные работы и резерв на проверку.";
    const base = d.project.revision;
    d.project.revision++;
    d.draft = {
      id: crypto.randomUUID(),
      proposal: p,
      validation: validate(p, d.project.state, d.progress),
      base_revision: base,
      accepted_revision: null,
      created_at: new Date().toISOString(),
    };
    d.plans.unshift(copy(d.draft));
    d.messages.push({ role: "assistant", text: p.summary });
    this.save();
  }
  accept() {
    const d = this.data;
    if (d.draft && samePlan(d.accepted.proposal, d.draft.proposal)) {
      throw Error("План не изменился; повторное принятие не требуется");
    }
    if (
      !d.draft ||
      !d.draft.validation.valid ||
      !proposalIsCurrent(d.draft, d.project, d.events)
    )
      throw Error("Нужен актуальный выполнимый план");
    this.event("plan_accepted", { plan_id: d.draft.id });
    d.draft.accepted_revision = d.project.revision;
    d.accepted = copy(d.draft);
    d.plans = d.plans.map((p) => (p.id === d.draft.id ? copy(d.draft) : p));
    d.draft = null;
    this.save();
  }
  task(id, patch) {
    const d = this.data,
      t = d.accepted.proposal.tasks.find((t) => t.id === id),
      flags = d.progress[id] || {
        done: false,
        pinned: false,
        spent_minutes: 0,
      };
    if (patch.done === true && !flags.done) {
      if (!t.depends_on.every((dep) => d.progress[dep]?.done))
        throw Error("Сначала заверши задачи, от которых зависит эта");
      flags.spent_minutes = patch.actual_minutes ?? t.estimate_minutes;
      flags.debited_minutes = flags.spent_minutes;
      d.timeBudget.charged += flags.debited_minutes;
      this.updateBudget();
    }
    if (patch.done === false && flags.done) {
      if (
        d.accepted.proposal.tasks.some(
          (t) => t.depends_on.includes(id) && d.progress[t.id]?.done,
        )
      )
        throw Error("Сначала верни зависимые задачи");
      d.timeBudget.charged -= flags.debited_minutes ?? flags.spent_minutes;
      this.updateBudget();
      flags.spent_minutes = 0;
      flags.debited_minutes = 0;
    }
    Object.assign(flags, patch);
    d.progress[id] = flags;
    this.event("task_changed", { task_id: id, ...patch });
    this.save();
  }
  renameProject(title) {
    title = title.trim();
    if (!title || title.length > 200)
      throw Error("Укажи название от 1 до 200 символов");
    const before = this.data.project.title;
    if (before === title) return;
    this.data.project.title = title;
    this.event("project_renamed", { before, title });
    this.save();
  }
  deleteProject() {
    this.data = { deleted: true, messages: [] };
    this.save();
  }
  restoreExample() {
    this.data = seed();
    this.data.plans = [copy(this.data.accepted)];
    this.data.timeBudget = {
      allocated: this.data.project.state.available_minutes,
      lost: 0,
      charged: 0,
    };
    this.save();
  }
}
