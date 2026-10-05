#!/usr/bin/env python3
"""Минимальный пример: один вызов JEV через OpenRouter.

ВНИМАНИЕ: каждый вызов с флагом --send стоит денег (списывается с вашего баланса
OpenRouter). Без флага --send скрипт ничего не отправляет и только показывает,
что отправил бы. Ключ в этот файл не вставляйте.

Как пользоваться:
  1. Положите ключ в переменную окружения (в Терминале Mac/Linux):
         export JEV_KEY="sk-or-..."
  2. Проверка без траты денег:
         python3 primer-zaprosa-jev.py voprosy/otzyvy-konkurentov.json "Текст одного отзыва"
  3. Реальный вызов, ровно один запрос:
         python3 primer-zaprosa-jev.py voprosy/otzyvy-konkurentov.json "Текст одного отзыва" --send

Только стандартная библиотека Python 3.9+, ничего ставить не нужно.
Данные уходят на сервис OpenRouter: не отправляйте паспортные данные, телефоны и фамилии.
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/v1/systemone"
MODEL = "~typesafe/jev-latest"


def main():
    args = [a for a in sys.argv[1:] if a != "--send"]
    send = "--send" in sys.argv[1:]
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)

    questions_path, state = args
    with open(questions_path, encoding="utf-8") as f:
        questions = json.load(f)
    # ключи с подчёркиванием в начале - это комментарии для вас, JEV их не получает
    questions = {k: v for k, v in questions.items() if not k.startswith("_")}

    if "{{" in json.dumps(questions, ensure_ascii=False):
        print("Остались неподставленные места {{...}} в файле вопросов. Заполните их и повторите.")
        sys.exit(1)

    body = {"model": MODEL, "state": state, "questions": questions}
    print(f"Вопросов: {len(questions)}. Один запрос на {URL}, модель {MODEL}.")

    if not send:
        print("Это пробный режим: ничего не отправлено, денег не потрачено.")
        print("Чтобы отправить один реальный запрос, добавьте флаг --send.")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return

    key = os.environ.get("JEV_KEY")
    if not key:
        print("Нет переменной окружения JEV_KEY. Положите ключ туда, не в файл и не в чат.")
        sys.exit(1)

    req = urllib.request.Request(
        URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            answer = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 401 неверный ключ, 402 кончился баланс, 403 доступ закрыт: останавливаемся, не повторяем
        print(f"Ошибка {e.code}. Проверьте ключ и баланс на openrouter.ai. Повторять запрос не нужно.")
        sys.exit(1)
    except Exception as e:
        print(f"Сеть или формат ответа: {e}")
        sys.exit(1)

    print(json.dumps(answer, ensure_ascii=False, indent=2))
    # choice: {choice, probabilities, confidence}; score: {score, probabilities, confidence}; noul: {noul}


if __name__ == "__main__":
    main()
