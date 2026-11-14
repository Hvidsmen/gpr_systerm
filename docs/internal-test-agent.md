# Внутренний агент проверки ТЕСТового портала

Агент запускает Chromium на Windows-компьютере внутри корпоративной сети. Портал не публикуется в интернете, входящие порты на компьютере открывать не нужно. Компьютер сам забирает задание из GitHub Actions и отправляет туда отчёт.

## Безопасные границы

- Сценарий требует на странице входа маркер `ТЕСТОВЫЙ ПОРТАЛ`. Без него пароль не вводится.
- Первый сценарий только входит в портал и открывает обзор; данные он не меняет.
- Нужна отдельная учётная запись ТЕСТового портала с минимальными правами. Не используйте рабочий пароль администратора.
- Не разрешайте запуск self-hosted runner из pull request недоверенных авторов.
- Компьютер должен быть выделен для проверок, на нём не должно быть постоянных пользовательских секретов.

## 1. Сетевой доступ

Компьютеру нужен исходящий HTTPS-доступ к GitHub Actions, GitHub API, хранилищу артефактов и npm. Если в сети есть белый список, используйте актуальный список доменов из документации GitHub **Communicating with self-hosted runners**. Минимум обычно включает:

- `github.com`, `api.github.com`, `*.actions.githubusercontent.com`;
- `codeload.github.com`, `objects.githubusercontent.com`, `raw.githubusercontent.com`;
- `github-releases.githubusercontent.com`, `*.blob.core.windows.net`;
- `registry.npmjs.org`, `playwright.azureedge.net`, `cdn.playwright.dev`.

## 2. Установка runner

1. В GitHub откройте **Settings → Actions → Runners → New self-hosted runner** для этого репозитория.
2. Скопируйте короткоживущий registration token.
3. На внутреннем компьютере откройте PowerShell **от имени администра** и запустите:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\ops\install-internal-test-agent.ps1 `
  -RepositoryUrl 'https://github.com/Hvidsmen/gpr_systerm' `
  -RegistrationToken '<короткоживущий-token>'
```

Скрипт скачает актуальную Windows x64 версию GitHub runner, добавит метку `gpr-internal` и зарегистрирует его как службу Windows.

## 3. Секреты ТЕСТового портала

В **Settings → Secrets and variables → Actions** добавьте repository secrets:

- `INTERNAL_TEST_BASE_URL` — адрес ТЕСТового портала, например `http://192.168.70.9:8002/`;
- `INTERNAL_TEST_USERNAME` — логин отдельного тестового пользователя;
- `INTERNAL_TEST_PASSWORD` — его пароль.

Значения не попадают в Git-репозиторий и не печатаются в отчёте.

## 4. Проверка

Откройте **Actions → Internal TEST portal check → Run workflow**. Также проверка автоматически запускается после публикации в ветку `codex/staging-portal-20261102`.

В запуске будут:

- краткий русский итог с подсказкой, что исправить;
- полный скриншот страницы в широком окне;
- скриншот мобильного вида;
- `diagnostics.json` с ошибками JavaScript и внутренних HTTP-запросов.

Артефакт хранится 14 дней. После установки можно добавить целевые сценарии для «Работ», «Людей», бригад, связей и сводного отчёта. Изменяющие данные сценарии должны запускаться только на ТЕСТовой базе.
