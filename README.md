# Инвентаризация ноутбуков — Telegram Mini App

Сотрудник открывает Mini App из Telegram, сканирует на ноутбуке QR-код или
штрих-код и сохраняет данные в базу. Ноутбук без кода добавляется вручную
через ту же форму. Доступ есть только у пользователей из белого списка,
который админ ведёт командами бота.

**Стек:** Python 3.12, FastAPI + uvicorn, SQLite, aiogram 3, фронтенд на
ванильном JS без сборки. Один процесс и один контейнер: веб-приложение и бот
(long polling) живут вместе.

---

## 1. Что потребуется

- Сервер с Docker и Docker Compose v2 (`docker compose version`).
- Домен с HTTPS-сертификатом, в примерах — `inv.example.com`.
  **Telegram не открывает Mini App по HTTP и по IP-адресу.** По той же причине
  нужен HTTPS и сканеру: доступ к камере браузеры дают только защищённым
  страницам.
- Токен бота от [@BotFather](https://t.me/BotFather).

---

## 2. Создание бота в BotFather

1. Напиши [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Задай имя и username (должен заканчиваться на `bot`).
3. Скопируй токен вида `123456789:AAAbbb...` — это `BOT_TOKEN`.
   Токен нигде не публикуем, он живёт только в `.env`.

Привязку кнопки меню к домену сделаем на шаге 6, когда приложение уже запущено.

---

## 3. Свой Telegram ID (ADMIN_IDS)

Нужен числовой ID, а не username. Два способа:

- Написать боту [@userinfobot](https://t.me/userinfobot) — он ответит твоим ID.
- Либо запустить приложение с любым значением `ADMIN_IDS` (например `1`),
  открыть Mini App — появится экран «Нет доступа» с твоим настоящим ID.
  Вписать его в `ADMIN_IDS` и перезапустить (`docker compose up -d`).

Админов может быть несколько: `ADMIN_IDS=111111111,222222222`.
Админ всегда имеет доступ к приложению, даже если его нет в белом списке.

---

## 4. Настройка окружения

```bash
git clone <репозиторий> laptop-inventory   # или просто скопируй каталог
cd laptop-inventory
cp .env.example .env
nano .env
```

Минимум, что нужно заполнить:

```ini
BOT_TOKEN=123456789:AAAbbbCCC...
ADMIN_IDS=111111111
WEBAPP_URL=https://inv.example.com
```

`.env` в `.gitignore` и в `.dockerignore` — в образ и в репозиторий он не попадает.

Полный список переменных:

| Переменная | Обязательна | По умолчанию | Назначение |
|---|---|---|---|
| `BOT_TOKEN` | да | — | Токен бота. Им же проверяется подпись `initData`. |
| `ADMIN_IDS` | да | — | Telegram ID админов через запятую. |
| `WEBAPP_URL` | да | — | Публичный HTTPS-адрес Mini App. |
| `DB_PATH` | нет | `/data/inventory.db` | Файл SQLite (в compose задан принудительно). |
| `PORT` | нет | `8080` | Порт внутри контейнера. Менять не нужно. |
| `INITDATA_MAX_AGE_SEC` | нет | `86400` | Максимальный возраст `initData` (24 ч). |
| `BOT_ENABLED` | нет | `true` | `false` — поднять только веб без бота. |
| `SET_MENU_BUTTON` | нет | `true` | Привязать кнопку меню бота к `WEBAPP_URL` при старте. |

---

## 5. Запуск

```bash
docker compose up -d --build
docker compose logs -f app
```

В логах должно появиться:

```
app.db: Применяю миграцию 0001_init.sql
app.main: База /data/inventory.db готова (применено миграций: 1)
app.main: Бот запущен (long polling)
app.main: Кнопка меню бота привязана к https://inv.example.com
```

Проверка здоровья: `curl http://127.0.0.1:8080/healthz` → `{"status":"ok"}`.

Приложение слушает только `127.0.0.1:8080` — наружу его публикует nginx.

---

## 6. Reverse-proxy (nginx) и привязка Mini App к домену

### Конфиг nginx

`/etc/nginx/sites-available/inv.example.com`:

```nginx
server {
    listen 80;
    server_name inv.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name inv.example.com;

    ssl_certificate     /etc/letsencrypt/live/inv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/inv.example.com/privkey.pem;

    # Telegram открывает Mini App в iframe (web-версия), поэтому НЕЛЬЗЯ
    # добавлять X-Frame-Options: DENY/SAMEORIGIN или CSP frame-ancestors.
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/inv.example.com /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Сертификат, если его ещё нет: `certbot --nginx -d inv.example.com`.

### Кнопка меню / Web App

При `SET_MENU_BUTTON=true` приложение само ставит кнопку меню бота на
`WEBAPP_URL` при старте — ничего делать не нужно.

Если ставишь вручную (`SET_MENU_BUTTON=false`):

1. [@BotFather](https://t.me/BotFather) → `/mybots` → твой бот → **Bot Settings**
   → **Menu Button** → **Configure menu button**.
2. Отправь URL `https://inv.example.com` и подпись кнопки, например
   `Инвентаризация`.

Проверка: открой диалог с ботом, нажми кнопку меню слева от поля ввода.

---

## 7. Первый пользователь

Админ (ты) уже имеет доступ через `ADMIN_IDS`. Чтобы пустить сотрудника:

1. Сотрудник узнаёт свой Telegram ID: открывает Mini App → экран «Нет доступа»
   показывает его ID, рядом кнопка «Скопировать ID». Либо через @userinfobot.
2. Ты пишешь боту:

```
/adduser 123456789 Иван Петров
```

Бот ответит «Доступ выдан: 123456789 (Иван Петров)». Сотруднику достаточно
переоткрыть Mini App.

### Команды бота (только для админов)

| Команда | Что делает |
|---|---|
| `/adduser <telegram_id> [имя]` | Выдать доступ. Имя необязательно. |
| `/removeuser <telegram_id>` | Отозвать доступ. |
| `/users` | Список админов и всех, кому выдан доступ. |

Доступ админа командой не отзывается — убери ID из `ADMIN_IDS` и перезапусти.

---

## 8. Работа в приложении

- **Сканировать** — открывает встроенный сканер, который читает и QR-коды, и
  штрих-коды. Выбирать тип кода не нужно: наводи камеру на любой из них.
  Распознанные поля подставляются в форму — её нужно проверить и сохранить
  вручную, сразу в базу ничего не пишется. Исходная строка кода всегда
  сохраняется в поле `qr_raw`, а в `source` пишется `qr` или `barcode`.
- **Добавить вручную** — та же форма, пустая. Обязателен серийный номер.
- Поля «Производитель», «Модель» и «Сотрудник» подсказывают значения, которые
  уже есть в базе. Штрих-код на наклейке обычно содержит только серийный номер, модель в
  него не попадает — поэтому первый ноутбук каждой модели заполняется руками,
  а дальше она выбирается из списка.
- Если такой серийный номер уже есть, приложение покажет существующую карточку
  и не создаст дубль (регистр и пробелы не важны).
- **Поиск** — переключатель над списком задаёт, где искать: «Везде» (серийник,
  сотрудник, модель, инв. номер), «Серийник» или «Сотрудник». Регистр и
  «ё»/«е» не важны: «ИВАНОВ», «иванов» и «Пётр»/«Петр» находят одно и то же.
- **Фильтр по статусу** — чипы под поиском с количеством ноутбуков каждого
  статуса (с учётом введённого запроса).
- **Сортировка** — «Сначала новые» или «По статусу»: во втором случае список
  разбит на группы Используется → Доступен → В ремонте → Списан.

### Что понимает сканер

Поддерживаемые типы кодов: QR, Data Matrix, Code 128, Code 39, Code 93, ITF,
Codabar, EAN-13, EAN-8, UPC-A, UPC-E. Этого набора хватает для наклеек
производителей и инвентарных меток.

Содержимое кода может быть в любом из четырёх форматов: JSON, пары
`ключ=значение` / `ключ: значение`, URL с параметрами или просто строка.
Строка считается серийным номером — именно так обычно устроены штрих-коды,
поэтому скан наклейки сразу заполняет поле «Серийный номер».

### Если камера недоступна

Сканер требует доступа к камере, и приложение само подбирает запасной вариант:

1. Встроенный сканер (камера) — основной путь, работает и для QR, и для
   штрих-кодов.
2. Нет доступа к камере, но клиент умеет `showScanQrPopup` — появится кнопка
   «Сканировать QR средствами Telegram». Нативный попап читает **только QR**,
   штрих-коды он не распознаёт.
3. Ни того, ни другого (например, Telegram Desktop без камеры) — приложение
   сообщит об этом и предложит ручной ввод.

Если камеру запретили, разрешение выдаётся в настройках телефона для
приложения Telegram, а не внутри Mini App.

Декодер (`web/vendor/zxing.min.js`, 328 КБ) лежит в репозитории и грузится
лениво — при первом нажатии «Сканировать», а не при каждом открытии
приложения. Подробности и порядок обновления — в `web/vendor/README.md`.

---

## 9. Бэкап и восстановление

SQLite нельзя копировать «на горячую» обычным `cp` — нужен снимок через
штатный API. Команда делает консистентную копию работающей базы:

```bash
docker compose exec -T app python -c "import sqlite3,os; src=sqlite3.connect(os.environ['DB_PATH']); dst=sqlite3.connect('/data/backup.db'); src.backup(dst); dst.close(); src.close()"
docker compose cp app:/data/backup.db ./inventory-$(date +%F).db
docker compose exec -T app rm /data/backup.db
```

В cron (ежедневно в 4:00, хранить 14 копий):

```cron
0 4 * * * cd /opt/laptop-inventory && docker compose exec -T app python -c "import sqlite3,os; src=sqlite3.connect(os.environ['DB_PATH']); dst=sqlite3.connect('/data/backup.db'); src.backup(dst); dst.close(); src.close()" && docker compose cp app:/data/backup.db /var/backups/inventory-$(date +\%F).db && docker compose exec -T app rm /data/backup.db && find /var/backups -name 'inventory-*.db' -mtime +14 -delete
```

Восстановление:

```bash
docker compose stop app
docker compose cp ./inventory-2026-10-16.db app:/data/inventory.db
docker compose start app
```

Данные лежат в docker-томе `inv-data` и переживают пересборку образа.
Посмотреть том: `docker volume inspect laptop-inventory_inv-data`.

---

## 10. Обновление

```bash
git pull
docker compose up -d --build
```

Миграции применяются автоматически при старте: раннер выполняет новые
`.sql` из `migrations/` по возрастанию имени и запоминает их в таблице
`schema_migrations`. Новая миграция — новый файл `migrations/0002_*.sql`,
существующие файлы не правим.

---

## 11. Локальная разработка

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt          # Windows: .venv\Scripts\pip

export BOT_TOKEN=123456:FAKE ADMIN_IDS=1000 BOT_ENABLED=false DB_PATH=data/dev.db
.venv/bin/uvicorn app.main:create_app --factory --reload --port 8080
```

Тесты (без сети и без реального бота):

```bash
.venv/bin/python -m pytest -q
```

Покрыто: валидная подпись `initData` принимается, поддельная и просроченная
отклоняются (401), пользователь вне белого списка получает 403 со своим ID,
парсер кодов на всех четырёх форматах, дубликат серийного номера не создаётся
(409 с существующей карточкой), команды бота работают только у админов,
миграция с пересозданием `laptops` не теряет данные и не ломает ограничения.

Сканер камерой тестами не покрыт — для него нужен браузер с камерой,
проверяется вручную на телефоне.

Открыть приложение в браузере напрямую нельзя: без `initData` оно покажет
«Это приложение открывается только из Telegram». Это ожидаемо — вся проверка
подписи делается на сервере.

---

## 12. Как устроен доступ

1. Фронтенд отправляет `Telegram.WebApp.initData` в заголовке
   `X-Telegram-Init-Data` на **каждый** запрос к `/api/*`.
2. Сервер считает `HMAC-SHA256` по официальному алгоритму Telegram
   (`secret_key = HMAC_SHA256("WebAppData", BOT_TOKEN)`) и сверяет с полем
   `hash`, затем проверяет свежесть `auth_date` (не старше 24 ч).
3. Только после проверки подписи `user.id` из **подписанной** строки сверяется
   с таблицей `allowed_users` и со списком `ADMIN_IDS`.
4. `initDataUnsafe` и любые ID из тела запроса игнорируются: подделать
   личность, не зная токена бота, нельзя.

Неподписанный или просроченный запрос → `401`. Подпись верна, но доступа нет →
`403` с Telegram ID в ответе, фронтенд показывает его на экране «Нет доступа».

---

## 13. Структура проекта

```
app/
  main.py      FastAPI, статика, lifespan (миграции + запуск бота)
  config.py    переменные окружения
  db.py        соединение с SQLite и раннер миграций
  auth.py      проверка initData и белого списка
  models.py    схемы запросов/ответов, статусы
  repo.py      SQL-запросы
  qr.py        парсер содержимого кодов
  api.py       эндпоинты /api/*
  bot.py       команды /adduser, /removeuser, /users
  errors.py    единый формат ошибок
migrations/    0001_init.sql — схема БД
               0002_source_barcode.sql — source='barcode'
web/           index.html, app.js, styles.css — Mini App
  vendor/      zxing.min.js — декодер QR и штрих-кодов (Apache-2.0)
tests/         pytest
```

### API

Все эндпоинты требуют заголовок `X-Telegram-Init-Data`.

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/healthz` | Проверка живости, без авторизации. |
| `GET` | `/api/me` | Текущий пользователь и признак админа. |
| `GET` | `/api/meta` | Статусы и их русские подписи. |
| `GET` | `/api/suggestions` | Производители, модели и сотрудники из базы — подсказки в форме. |
| `GET` | `/api/laptops?q=&by=&status=&sort=&limit=&offset=` | Список. `by`: `all` / `serial` / `employee`; `status`: фильтр по коду статуса; `sort`: `new` / `status`. В ответе `status_counts` — сколько найдено каждого статуса. |
| `GET` | `/api/laptops/{id}` | Карточка ноутбука. |
| `POST` | `/api/laptops` | Создать. Дубликат серийника → `409` + существующая запись. |
| `PATCH` | `/api/laptops/{id}` | Изменить (`qr_raw` и `source` неизменяемы). |
| `POST` | `/api/qr/parse` | Разобрать содержимое кода (QR или штрих-кода), ничего не сохраняя. |

### Таблицы

`laptops`: `id`, `manufacturer`, `model`, `serial_number` (unique, обязателен),
`inventory_number`, `status` (`in_use` — «Используется», `in_stock` — «Доступен»,
`repair` — «В ремонте», `retired` — «Списан»),
`assigned_to`, `location`, `notes`, `qr_raw`, `source`
(`qr` / `barcode` / `manual`), `created_by_tg_id`, `created_at`, `updated_at`.

`qr_raw` хранит исходную строку любого отсканированного кода, не только QR —
имя поля осталось с первой версии схемы.

`allowed_users`: `telegram_id` (unique), `name`, `added_by`, `created_at`.

---

## 14. Если что-то не работает

| Симптом | Причина и что делать |
|---|---|
| Кнопка меню открывает пустой экран | Нет HTTPS или сертификат невалиден. Проверь `curl -I https://inv.example.com`. |
| Mini App не открывается в web-версии Telegram | nginx отдаёт `X-Frame-Options` или CSP `frame-ancestors`. Убери их для этого домена. |
| «Это приложение открывается только из Telegram» | Страницу открыли в обычном браузере — так и должно быть. |
| 401 на всех запросах | `BOT_TOKEN` в `.env` не от того бота, через которого открывают приложение. |
| «Нет доступа» у себя же | Твой ID не попал в `ADMIN_IDS`, либо после правки `.env` не было `docker compose up -d`. |
| Бот молчит на команды | `BOT_ENABLED=false`, либо на этот токен уже настроен webhook другим приложением — смотри `docker compose logs app`. |
| «Нет доступа к камере» | Разреши камеру для Telegram в настройках телефона (внутри Mini App это не настраивается), затем переоткрой приложение. |
| Сканер не открывается вовсе | У клиента нет камеры (обычно Telegram Desktop) — открой с телефона или добавь вручную. |
| Штрих-код не читается, QR читается | Нажата кнопка «Сканировать QR средствами Telegram»: нативный попап понимает только QR. Вернись назад и запусти встроенный сканер. |
| Сканер долго грузится при первом запуске | Декодер в 328 КБ скачивается один раз при первом сканировании, дальше берётся из кеша. |
| Кнопка меню не привязалась | В логах будет предупреждение: проверь, что `WEBAPP_URL` — публичный `https`-адрес. |
