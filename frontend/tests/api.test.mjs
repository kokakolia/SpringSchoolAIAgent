import test from "node:test";
import assert from "node:assert/strict";
import { createApi, ApiError } from "../dist/api.js";

test("API preserves structured errors so busy operations keep their request ID", async () => {
  const api = createApi({
    fetcher: async () =>
      new Response(
        JSON.stringify({ detail: { message: "Busy", code: "operation_busy" } }),
        { status: 409 },
      ),
  });
  await assert.rejects(
    api("/api/projects", {}),
    (error) =>
      error instanceof ApiError &&
      error.status === 409 &&
      error.code === "operation_busy" &&
      error.message === "Busy",
  );
});
test("validation errors and retry delay remain understandable", async () => {
  const api = createApi({
    fetcher: async () =>
      new Response(
        JSON.stringify({ detail: [{ msg: "Value error, Invalid title" }] }),
        {
          status: 429,
          headers: { "Retry-After": "30" },
        },
      ),
  });
  await assert.rejects(
    api("/api/projects", {}),
    (error) => error.message === "Invalid title" && error.retryAfter === 30,
  );
});
test("network and non-JSON failures produce useful messages", async () => {
  const offline = createApi({
    fetcher: async () => {
      throw new TypeError("Failed to fetch");
    },
  });
  await assert.rejects(offline("/api/projects"), /Нет связи/);
  const invalid = createApi({
    fetcher: async () =>
      new Response("<html>Proxy error</html>", { status: 502 }),
  });
  await assert.rejects(invalid("/api/projects"), /неожиданный ответ/);
});
test("request aborts at the deadline without leaving a timer", async () => {
  const api = createApi({
    timeoutMs: 5,
    fetcher: async (_path, { signal }) =>
      new Promise((_resolve, reject) =>
        signal.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        ),
      ),
  });
  await assert.rejects(api("/api/projects"), /не ответил вовремя/);
});
