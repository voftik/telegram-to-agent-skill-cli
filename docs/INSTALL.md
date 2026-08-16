# Установка на новой машине

Цель: от `git clone` до работающего CLI и авто-активирующегося скилла —
≤ 10 минут.

## Предпосылки

- Python ≥ 3.10 и [uv](https://docs.astral.sh/uv/)
- Свои Telegram API-креды: https://my.telegram.org → **API development
  tools** → создать приложение → получить `api_id` и `api_hash`.
  Не используйте чужие/дефолтные креды — это повышает риск ограничений
  аккаунта.

## Быстрый путь

```bash
git clone https://github.com/voftik/telegram-to-agent-skill-cli.git
cd telegram-to-agent-skill-cli
./install.sh
```

`install.sh` идемпотентен (можно перезапускать) и делает:

1. `uv tool install --editable .` → команда `tg`
2. создаёт каталог данных `~/Library/Application Support/tg-cli/`
   (права 700) и заготовку `.env` (700/600)
3. симлинки скилла: `repo/skill` → `~/.agents/skills/tg` →
   `~/.claude/skills/tg`
4. добавляет блоки авто-активации в `~/.claude/CLAUDE.md` (Claude Code)
   и `~/.codex/AGENTS.md` (Codex, если установлен)
5. самопроверка `tg status`

## Ручные шаги после установщика

```bash
# 1. Впишите креды
nano ~/Library/Application\ Support/tg-cli/.env   # TG_API_ID, TG_API_HASH

# 2. Авторизация: телефон → код в приложении Telegram → пароль 2FA
tg whoami

# 3. Первичный синк (на большом аккаунте — часы). Команда сама ставит
#    себя в автозагрузку, переживает перезагрузки и сбои, а после первого
#    полного прохода снимает себя с дежурства:
tg bootstrap start
# прогресс: tg bootstrap status · лог: <data>/bootstrap.log · отмена: tg bootstrap stop

# 4. Проверка
tg status
tg brief <любой чат>
```

## Безопасность

- **Session-файл** `~/Library/Application Support/tg-cli/tg_cli.session` =
  полный доступ к аккаунту. Не копировать между машинами, не класть в
  облачные папки, не коммитить. На каждой машине — свой вход по коду.
- `.env`, `*.session`, `*.db`, `files/` закрыты `.gitignore`.
- Отправка сообщений возможна только с флагом `--confirm`; каждая отправка
  логируется в `sent.log` в каталоге данных.
- Каталог данных живёт вне облачной синхронизации сознательно: SQLite +
  облачные диски несовместимы, а переписке нечего делать на чужих серверах.

## Обновление

```bash
cd telegram-to-agent-skill-cli && git pull
# editable-установка подхватывает изменения кода автоматически;
# при изменении зависимостей: uv tool install --reinstall --editable .
```

## Linux/Windows

Каталог данных выбирается платформенно (XDG на Linux, LOCALAPPDATA на
Windows); путь скиллов Claude Code тот же (`~/.claude/skills`). install.sh
рассчитан на macOS/Linux; на Windows выполните шаги вручную.

## Способ доставки скилла

Поддерживаемый способ установки скилла — клон репозитория (или распакованный sdist) + `./install.sh`. Wheel содержит только runtime-код CLI и скилл не включает; sdist — полный срез исходников, состав проверяется в CI (`scripts/check_dist.py`).
