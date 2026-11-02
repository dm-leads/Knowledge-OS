"""Помощник заголовков для http-MCP-серверов (headersHelper Claude Code).

Читает токен из файла секретов по имени переменной и печатает в stdout JSON
{"Authorization": "Bearer <токен>"}. Stdout забирает Claude Code, в чат он не попадает.

Запуск: python mcp_bearer_header.py <ИМЯ_ПЕРЕМЕННОЙ>
Файл секретов: переменная окружения SECRETS_ENV_FILE, иначе путь по умолчанию ниже.
Порядок поиска: сначала окружение процесса, потом файл секретов.
Значение токена никогда не печатается в stderr и в сообщения об ошибках.
"""
import json
import os
import sys
from pathlib import Path

# Файл секретов устройства по умолчанию; другой путь — переменная SECRETS_ENV_FILE
DEFAULT_ENV_FILE = str(Path.home() / "Second Brain Secrets" / ".env")


def read_from_env_file(path: str, name: str) -> str | None:
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("export "):
                    s = s[len("export "):].lstrip()
                key, sep, value = s.partition("=")
                if sep and key.strip() == name:
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    return value
    except OSError as e:
        sys.stderr.write(f"mcp_bearer_header: cannot open secrets file ({e.__class__.__name__})\n")
    return None


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("mcp_bearer_header: usage: mcp_bearer_header.py <VAR_NAME>\n")
        return 2
    name = sys.argv[1]
    token = os.environ.get(name) or read_from_env_file(
        os.environ.get("SECRETS_ENV_FILE", DEFAULT_ENV_FILE), name
    )
    if not token:
        sys.stderr.write(f"mcp_bearer_header: variable {name} not found or empty\n")
        return 1
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    sys.stdout.write(json.dumps({"Authorization": f"Bearer {token}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
