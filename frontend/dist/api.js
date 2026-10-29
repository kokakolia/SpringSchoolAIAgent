export class ApiError extends Error {
  constructor(message, status = 0, code = null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function createApi({
  fetcher = globalThis.fetch,
  timeoutMs = 195000,
} = {}) {
  return async (path, body, method = "POST") => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetcher(path, {
        method: body === undefined ? "GET" : method,
        headers:
          body === undefined
            ? undefined
            : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      let data;
      try {
        data = await response.json();
      } catch {
        throw new ApiError("Сервер вернул неожиданный ответ", response.status);
      }
      if (!response.ok) {
        const detail = data?.detail;
        const message = Array.isArray(detail)
          ? detail
              .map((error) => String(error.msg).replace(/^Value error, /, ""))
              .join("; ")
          : typeof detail === "object" && detail !== null
            ? detail.message
            : detail;
        const error = new ApiError(
          message || `Ошибка ${response.status}`,
          response.status,
          detail?.code,
        );
        error.retryAfter = Number(response.headers.get("retry-after")) || null;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError")
        throw new ApiError(
          "Сервер не ответил вовремя. Проверь состояние перед повтором.",
        );
      if (error instanceof TypeError)
        throw new ApiError(
          "Нет связи с сервером. Проверь подключение и повтори.",
        );
      throw error;
    } finally {
      clearTimeout(timer);
    }
  };
}
