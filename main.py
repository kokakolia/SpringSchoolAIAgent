import sys
from agent import TaskMasterAgent


def run_baseline(text):
    """
    Простое решение (Baseline):
    Просто разбивает текст на предложения и считает их задачами без приоритетов.
    """
    print("\n--- [BASELINE] Простое извлечение строк ---")
    tasks = text.replace("!", ".").replace("?", ".").replace('.', ',').split(',')
    # tasks = [s.strip() for s in sentences if len(s.strip()) > 5]
    for i, t in enumerate(tasks):
        print(f"{i}. {t} (Приоритет: Не определен)")
    return tasks


def main():
    print("=== AI Task Master: Агент оптимизации планирования ===")
    print("Введите ваш поток мыслей (дела, дедлайны, планы) или 'exit' для выхода:")

    agent = TaskMasterAgent()

    while True:
        user_input = input("\nВы: ").strip()

        if user_input.lower() in ['exit', 'quit', 'выход']:
            print("Удачи в делах! Пока.")
            break

        if not user_input:
            continue

        print("\n" + "-" * 30)
        # 1 baseline
        run_baseline(user_input)

        # 2 агент
        print("\n--- [AGENT] Интеллектуальное планирование ---")
        try:
            final_response = agent.run(user_input)

            print("\nФИНАЛЬНЫЙ ОТВЕТ АГЕНТА:")
            print(final_response)
        except Exception as e:
            print(f"Произошла ошибка: {e}")

        print("\n" + "=" * 50)
        print("Готов к следующему запросу!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПрограмма остановлена.")
        sys.exit(0)