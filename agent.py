import json
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from tools import get_current_time, save_tasks_to_matrix, TOOLS_DESCRIPTION
from dotenv import load_dotenv
import os


load_dotenv()
KEY = os.getenv('GIGACHAT_KEY')


class TaskMasterAgent:
    def __init__(self):

        if not KEY:
            raise ValueError("API ключи не найден! Проверьте файл .env")

        self.giga = GigaChat(
            credentials=KEY,
            verify_ssl_certs=False,
            scope='GIGACHAT_API_PERS'
        )
        self.history = [
            Messages(
                role=MessagesRole.SYSTEM,
                content=(
                    "Ты — помощник по тайм-менеджменту. Твоя задача:\n"
                    "1. Если дата неизвестна, узнай её через get_current_time.\n"
                    "2. Выдели задачи из текста и классифицируй их (квадранты 1-4).\n"
                    "3. Квадрант 1: Срочно и Важно (дедлайны до 2-3 дней, критические задачи).\n"
                    "4. Квадрант 2: Важно, но не срочно (стратегия, обучение, дедлайны далеко).\n"
                    "5. Квадрант 3: Срочно, но не важно (бытовуха, мелкие просьбы, еда).\n"
                    "6. Квадрант 4: Не важно и не срочно (развлечения, отдых).\n"
                    "7. Ты не должен придумывать за пользователя задачи, если в сообщении нет четких планов, просто игнорируй."
                    "8. Вызови save_tasks_to_matrix ОДИН РАЗ, чтобы сохранить все найденные задачи.\n"
                    "9. После сохранения напиши пользователю краткий отчет."
                )
            )
        ]

    def _execute_tool(self, tool_call):
        """Метод для маппинга названия функции из LLM на реальный код"""
        fun_name = tool_call.name

        args_string = tool_call.arguments

        if isinstance(args_string, str):
            args = json.loads(args_string)
        else:
            args = args_string

        print(f"--- Вызываю инструмент: {fun_name} с параметрами {args} ---")

        if fun_name == "get_current_time":
            return get_current_time()
        elif fun_name == "save_tasks_to_matrix":
            return save_tasks_to_matrix(args["tasks"])
        return "Ошибка: инструмент не найден"

    def run(self, user_input: str, max_turns=5):
        self.history.append(Messages(role=MessagesRole.USER, content=user_input))

        for _ in range(max_turns):
            payload = Chat(
                messages=self.history,
                functions=TOOLS_DESCRIPTION,
                function_call="auto"
            )
            response = self.giga.chat(payload)
            message = response.choices[0].message
            if message.function_call:
                tool_call = message.function_call
                result = self._execute_tool(tool_call)
                function_result_json = json.dumps(result, ensure_ascii=False)

                self.history.append(message)
                self.history.append(Messages(
                    role=MessagesRole.FUNCTION,
                    content=function_result_json,
                    name=tool_call.name
                ))
                continue
            else:
                self.history.append(message)
                return message.content


if __name__ == "__main__":
    agent = TaskMasterAgent()
    user_text = "ответить на сообщение, купить пельмени в магазине, потом сварить"
    print(f"Пользователь: {user_text}")
    final_reply = agent.run(user_text)
    print(f"Агент: {final_reply}")