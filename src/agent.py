import httpx

from src.config import settings
from src.logger import logger
from src.tools import TOOLS_DESCRIPTION, get_current_time, save_tasks_to_matrix

MODEL_URI = f"gpt://{settings.yandex_folder_id}/yandexgpt/latest"
API_URL = "https://ai.api.cloud.yandex.net/foundationModels/v1/completion"

SYSTEM_PROMPT = (
    "Ты — помощник по тайм-менеджменту. Твоя задача:\n"
    "1. Если дата неизвестна, узнай её через get_current_time.\n"
    "2. Выдели задачи из текста и классифицируй их (квадранты 1-4).\n"
    "3. Квадрант 1: Срочно и Важно (дедлайны до 2-3 дней, критические задачи).\n"
    "4. Квадрант 2: Важно, но не срочно (стратегия, обучение, дедлайны далеко).\n"
    "5. Квадрант 3: Срочно, но не важно (бытовуха, мелкие просьбы, еда).\n"
    "6. Квадрант 4: Не важно и не срочно (развлечения, отдых).\n"
    "7. Не придумывай задачи за пользователя. Если нет чётких планов — игнорируй.\n"
    "8. Если не можешь определить срочность/важность — спроси пользователя.\n"
    "9. Вызови save_tasks_to_matrix ОДИН РАЗ, сохрани все найденные задачи.\n"
    "10. После сохранения напиши пользователю краткий отчёт."
)


class TaskMasterAgent:
    def __init__(self, user_id: int = 1):
        if not settings.yandex_api_key or not settings.yandex_folder_id:
            raise ValueError("YANDEX_API_KEY и YANDEX_FOLDER_ID должны быть в .env")
        self.user_id = user_id
        self.history: list[dict] = [{"role": "system", "text": SYSTEM_PROMPT}]

    @classmethod
    async def run_once(cls, user_input: str, user_id: int = 1, max_turns=5) -> str:
        agent = cls(user_id=user_id)
        return await agent.run(user_input, max_turns)

    async def _call_api(self, messages: list[dict]) -> dict:
        payload = {
            "modelUri": MODEL_URI,
            "completionOptions": {
                "stream": False,
                "temperature": 0.3,
                "maxTokens": "2000",
            },
            "messages": messages,
            "tools": TOOLS_DESCRIPTION,
        }
        headers = {
            "Authorization": f"Api-Key {settings.yandex_api_key}",
            "x-folder-id": settings.yandex_folder_id,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(API_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json()

    async def _execute_tool(self, func_name: str, func_args: dict) -> str:
        logger.info("Calling tool: %s %s", func_name, func_args)
        if func_name == "get_current_time":
            return get_current_time()
        elif func_name == "save_tasks_to_matrix":
            return await save_tasks_to_matrix(func_args["tasks"], user_id=self.user_id)
        return "Ошибка: инструмент не найден"

    async def run(self, user_input: str, max_turns=5) -> str:
        self.history.append({"role": "user", "text": user_input})

        for turn in range(max_turns):
            logger.info("API call turn %d/%d", turn + 1, max_turns)
            data = await self._call_api(self.history)
            alternative = data["result"]["alternatives"][0]
            message = alternative["message"]
            status = alternative["status"]

            if status == "ALTERNATIVE_STATUS_TOOL_CALLS":
                tool_calls = message["toolCallList"]["toolCalls"]
                self.history.append(
                    {"role": "assistant", "toolCallList": {"toolCalls": tool_calls}}
                )

                tool_results = []
                for tc in tool_calls:
                    fc = tc["functionCall"]
                    result = await self._execute_tool(fc["name"], fc["arguments"])
                    tool_results.append(
                        {
                            "functionResult": {
                                "name": fc["name"],
                                "content": result,
                            }
                        }
                    )

                self.history.append(
                    {"role": "user", "toolResultList": {"toolResults": tool_results}}
                )
                continue

            text = message.get("text", "")
            self.history.append({"role": "assistant", "text": text})
            logger.info("Agent response: %s", text[:80])
            return text

        return "Агент не смог завершить обработку за отведённое число шагов."
