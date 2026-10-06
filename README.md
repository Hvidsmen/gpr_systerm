# ГПР Система

Приложение для планирования строительных работ и учёта выполнения: проекты,
объекты, разделы, работы и подработы, календари, профили нагрузки, версии планов,
люди, техника, топливо и аналитика. Python 3.12, Django 5.2, SQLite;
интерфейс на Django-шаблонах и Bootstrap. Для стилей Bootstrap нужен доступ
браузера к `cdn.jsdelivr.net`.

## Новая схема планирования

Основной процесс — рабочее пространство строительного объекта на произвольный
период. Введите базовые работы и ресурсы по месяцам, сформируйте дневные планы
и утвердите базу. Затем создавайте месячные уточнения полного плана по одному
из двух сценариев: прошлое по факту с перераспределением остатка либо
сохранение базы до и после выбранного месяца.

Пошаговая инструкция, примеры, выдача плана и обновление Windows:
[docs/period-workspace.md](docs/period-workspace.md).
Предыдущие сводные глобальные версии, виды работ и накопительные расчёты:
[docs/planning.md](docs/planning.md). **Перед обновлением существующей базы
сохраните её резервную копию.**

## Запуск на Windows (PowerShell)

Выполняйте команды в папке проекта с `manage.py`. Установите Python 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
if (!(Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force data | Out-Null
if ((Test-Path db.sqlite3) -and !(Test-Path data/db.sqlite3)) {
    Copy-Item db.sqlite3 data/db.sqlite3
}
$env:DJANGO_DB_PATH = "data/db.sqlite3"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver
```

Откройте `http://127.0.0.1:8000/`. Команды копируют исходную базу в локальную
папку `data` и не заменяют уже существующую копию. Для последующих запусков
запишите `DJANGO_DB_PATH=data/db.sqlite3` в `.env`. Перед миграцией собственной
рабочей базы остановите сервер и сохраните её отдельную резервную копию.
Исходная `db.sqlite3` пока остаётся в Git для сохранения существующих данных;
новые базы, загрузки и секреты игнорируются. Не добавляйте рабочую базу в коммиты.

Если в новой базе ещё нет администратора:

```powershell
.\.venv\Scripts\python.exe manage.py createsuperuser
```

Через `/admin/` создайте компанию, роли с кодами `ADMIN`, `PLANNER`, `MANAGER`,
`FOREMAN` и назначьте пользователям компанию и роль. Глобальная админ-панель
доступна только суперпользователям. После регистрации без назначенной компании
пользователь имеет доступ к настройкам профиля, но не к бизнес-данным.
Подробная политика: [docs/access-control.md](docs/access-control.md).

## Linux и облачная среда

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
mkdir -p data
# Если нужно перенести исходные данные и локальная база ещё не существует:
if [ -f db.sqlite3 ] && [ ! -f data/db.sqlite3 ]; then cp db.sqlite3 data/db.sqlite3; fi
export DJANGO_DB_PATH=data/db.sqlite3
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

В подготовленной облачной среде зависимости расположены в `/workspace/gpr-dev/venv`,
рабочая база — в `/workspace/gpr-dev/development.sqlite3`. Из `/workspace/gpr_systerm`:

```bash
export PYTHONPATH=/workspace/gpr-dev
/workspace/gpr-dev/venv/bin/python manage.py runserver 127.0.0.1:8000 --settings=cloud_settings --noreload
```

## Проверки и зависимости

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

Тесты используют отдельную тестовую БД. Проверяются разделение компаний,
формы профиля, генерация и согласование планов, завершение без изменения
baseline, ревизии и ввод факта, накопительный расчёт, глобальные снимки,
их согласование и перенос конфликтующих ресурсных записей. `requirements.txt` задаёт допустимые диапазоны,
`requirements.lock` содержит проверенные точные версии, включая зависимости Django.
Обновляйте lock-файл в отдельном виртуальном окружении и повторяйте тесты.
GitHub Actions выполняет эти проверки на Python 3.12.

## Рабочий сервер

`runserver` предназначен для разработки. Для публикации используйте WSGI/ASGI
сервер за HTTPS-прокси и настройте выдачу `staticfiles` и пользовательских файлов.
Конкретный сервер, домен и сертификат настраиваются при развёртывании.

Переменные читаются из окружения или локального `.env` (окружение имеет приоритет):

| Переменная | Назначение |
| --- | --- |
| `DJANGO_DEBUG` | `1` для разработки; обязательно `0` на рабочем сервере |
| `DJANGO_SECRET_KEY` | Новый случайный секрет длиной не менее 50 символов; обязателен при `DEBUG=0` |
| `DJANGO_ALLOWED_HOSTS` | Конкретные домены через запятую, например `gpr.example.org`; `*` запрещён при `DEBUG=0` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Доверенные HTTPS-источники через запятую, если они нужны |
| `DJANGO_DB_PATH` | Путь к базе вне Git; относительный путь считается от корня проекта |
| `DJANGO_TRUST_PROXY_SSL` | `1` только за доверенным прокси, который удаляет клиентский `X-Forwarded-Proto` и выставляет свой |
| `DJANGO_SECURE_SSL_REDIRECT` | По умолчанию включён при `DEBUG=0`; на рабочем сервере оставьте включённым |
| `DJANGO_HSTS_INCLUDE_SUBDOMAINS` / `DJANGO_HSTS_PRELOAD` | Включайте только после проверки HTTPS всего домена и поддоменов |

При `DEBUG=0` включаются HTTPS-перенаправление, secure cookies и HSTS.
Без ключа или списка разрешённых доменов приложение откажется запускаться.
В разработке без заданного ключа создаётся локальный `.dev-secret-key`;
он сохраняется между перезапусками и игнорируется Git.

Ранее ключ Django находился в исходниках. Для рабочего сервера создайте новый
ключ и обновите его в защищённых настройках: старый остаётся в истории Git.
Генерируйте секрет локально, не отправляйте его в чат и не добавляйте в Git.
Готовый экспорт `gpr_export.txt` исключён из репозитория, чтобы не хранить
устаревшие копии кода и ключей.

Перед публикацией выполните `python manage.py check --deploy`. Замечания про
HSTS-поддомены и preload требуют решения с учётом вашего домена. Проверка Django
не заменяет проверку реального HTTPS-прокси, резервного копирования и прав доступа.

## Структура

`apps/accounts` — пользователи; `projects` — объекты; `works` — работы;
`planning` — планы и расчёты; `production` — ресурсы и факты;
`resources` — справочники; `analytics` — показатели; `audit` — модель журнала.
В `planning` обработчики календарей, профилей и согласования выделены в отдельные
модули; расчёты находятся в `services.py`. Настройки проекта — в `config`.

Единый ввод фактов: [Факт объекта за день](docs/day-facts.md).

Роли и назначение объектов мастерам: [Доступ к данным](docs/access-control.md).
