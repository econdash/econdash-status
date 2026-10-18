#!/usr/bin/env python3
"""Upptime issue event -> Telegram rich message in Russian (sendRichMessage, sendMessage fallback).

    python3 .github/scripts/telegram_alert.py "$GITHUB_EVENT_PATH"          # sends
    python3 .github/scripts/telegram_alert.py event.json --dry-run          # prints the HTML
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

STATUS_PAGE = "https://status.econdash.org"

# What the HTTP code of a failed check usually means, for the person on call.
HINTS = {
    "0": "Сервер не ответил совсем: упал, завис, не открывается DNS или порт. Проверить, жив ли VPS, "
         "nginx и сервисы (pm2 dashboard, systemd econ-api).",
    "5": "Сервер ответил ошибкой 5xx: приложение упало или не достучалось до базы. "
         "Смотреть журнал econ-api (journalctl -u econ-api) или pm2 logs dashboard.",
    "4": "Код 4xx вместо ожидаемого: возможно, адрес изменился или запрос режет защита от ботов в nginx.",
    "2": "Сервер ответил, но в ответе нет ожидаемого текста: сломались данные или формат ответа. "
         "Открыть адрес вручную и сравнить с проверкой в .upptimerc.yml.",
}


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def _table(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr><td align="left">{html.escape(k)}</td><td align="left">{html.escape(v)}</td></tr>\n' for k, v in rows
    )
    return '<table border="1">\n<tr><th align="left">Что</th><th align="left">Значение</th></tr>\n' + body + "</table>"


def build(event: dict) -> str:
    issue = event["issue"]
    title = issue["title"]
    degraded = "degraded performance" in title
    name = re.sub(r"^\W+\s*", "", title)
    name = re.sub(r" (is down|has degraded performance)$", "", name)
    body = issue.get("body") or ""
    url = re.search(r"\((https?://[^)\s]+)\)\s+(?:was|experienced)", body)
    code = re.search(r"HTTP code: (\d+)", body)
    response_ms = re.search(r"Response time: (\d+) ms", body)
    opened = _time(issue["created_at"])

    rows = [("Проверка", name), ("Адрес", url.group(1) if url else "—")]
    if event["action"] == "opened":
        icon, state = ("🟡", "Медленно отвечает") if degraded else ("🔴", "Не работает")
        what = (
            "Ответ дольше допустимого времени (maxResponseTime в .upptimerc.yml)."
            if degraded
            else "Внешняя проверка с серверов GitHub не получила ожидаемый ответ после 3 попыток."
        )
        rows += [
            ("HTTP-код", code.group(1) if code else "—"),
            ("Время ответа", f"{response_ms.group(1)} мс" if response_ms else "—"),
            ("Началось", f"{opened:%Y-%m-%d %H:%M} UTC"),
        ]
        hint = HINTS.get((code.group(1) if code else "0")[0], HINTS["0"]) if not degraded else (
            "Сервер перегружен или медленно отвечает база. Посмотреть нагрузку (htop) и журнал сервиса."
        )
        parts = [
            f"<h3>{icon} [external] {state}: {html.escape(name)}</h3>",
            f"<p>{what}</p>",
            _table(rows),
            f"<details>\n<summary><b>Что это значит и что проверить</b></summary>\n<p>{html.escape(hint)}</p>\n</details>",
        ]
    else:
        closed = _time(issue["closed_at"])
        rows += [
            ("Началось", f"{opened:%Y-%m-%d %H:%M} UTC"),
            ("Закончилось", f"{closed:%Y-%m-%d %H:%M} UTC"),
            ("Длительность", _duration((closed - opened).total_seconds())),
        ]
        state = "Снова отвечает быстро" if degraded else "Снова работает"
        parts = [f"<h3>🟢 [external] {state}: {html.escape(name)}</h3>", _table(rows)]
    parts.append(
        "<hr/>\n<p>"
        f"🔗 <b>Инцидент:</b> {html.escape(issue['html_url'])}<br/>\n"
        f"📊 <b>Статус всех сервисов:</b> {STATUS_PAGE}"
        "</p>"
    )
    return "\n\n".join(parts)


def _post(method: str, payload: dict) -> bool:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001 - report and fall back
        print(f"{method} failed: {exc}", file=sys.stderr)
        return False


def send(rich: str) -> bool:
    ok = True
    for chat_id in os.environ["TELEGRAM_CHAT_IDS"].split(","):
        chat_id = chat_id.strip()
        if _post("sendRichMessage", {"chat_id": chat_id, "rich_message": {"html": rich}}):
            continue
        plain = re.sub(r"<[^>]+>", "", re.sub(r"</(?:h3|p|tr|table|details|summary)>|<br/>|<hr/>", "\n", rich))
        plain = re.sub(r"\n{3,}", "\n\n", html.unescape(plain)).strip()
        ok = _post("sendMessage", {"chat_id": chat_id, "text": plain, "disable_web_page_preview": True}) and ok
    return ok


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as f:
        message = build(json.load(f))
    if "--dry-run" in argv:
        print(message)
        return 0
    return 0 if send(message) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
