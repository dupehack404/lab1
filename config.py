# config.py — все константы и БД (aiogram v3)
import aiosqlite

# ====== ТОКЕН БОТА ======
BOT_TOKEN = "8465643872:AAHqZXr_7_HKOL0uckoDjiFxtW3f0uG--Vw"

# ====== ПУТИ/ИЗОБРАЖЕНИЕ ДЛЯ /start ======
# Локальный файл (png/jpg). Можно относительный путь — лучше абсолютный.
START_IMAGE_PATH = "assets/start.png"
# Резервный вариант — картинка по URL. Если пусто и файла нет — отправится только текст.
START_IMAGE_URL = ""  # например: "https://example.com/start.png"

# ====== ИДЫ ЧАТОВ ======
# Группа/беседа модерации (куда падают заявки) и канал публикаций
MODERATION_CHAT_ID = -5004252082
PUBLISH_CHANNEL_ID = -1003026579376

# ====== СТАРТОВЫЕ INLINE-КНОПКИ (2 ссылки) ======
BUTTONS = [
    {"text": "Ознакомиться с офертой", "url": "https://t.me/makintoshit"},
    {"text": "Телеграм-канал",         "url": "https://t.me/goosebump3s"},
]

# ====== КНОПКА «ПРИНЯТЬ» ======
ACCEPT_BUTTON_TEXT   = "Принять"
ACCEPT_CALLBACK_DATA = "accept"

# ====== ПРИВЕТСТВИЕ ПОСЛЕ ПРИНЯТИЯ ======
WELCOME_TEXT = (
    "Приветствуем в нашем сервисе для оказания качественных и выгодных сделок. "
    "В нижнем меню вы можете создать запрос на покупку, а так же настроить ваш профиль."
)

# ====== НИЖНИЕ REPLY-КНОПКИ (ГЛАВНОЕ МЕНЮ) ======
REPLY_BUTTONS = ["Мои запросы", "Мой профиль", "Помощь"]

# ====== ПОМОЩЬ (юзернеймы для ссылок в кнопках) ======
HELP_SUPPORT_USERNAME = "usertexpodderzhki"
HELP_NEWS_USERNAME    = "someout"
HELP_OFFERS_USERNAME  = "someout_offers"
HELP_ADS_USERNAME     = "makintoshit"

# ====== ПУТЬ К БАЗЕ ДАННЫХ ======
DB_PATH = "bot.db"


# ======================================================================
# ИНИЦИАЛИЗАЦИЯ/МИГРАЦИИ БАЗЫ ДАННЫХ (идемпотентно, вызывать при старте)
# ======================================================================
async def init_db() -> None:
    """
    Создаёт таблицы при первом запуске и дотягивает недостающие колонки при обновлениях.
    Безопасно вызывать на каждом старте.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # ---- user_profile: профили пользователей/статистика/контакты/реквизиты ----
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id        INTEGER PRIMARY KEY,

                -- флаг принятой оферты (чтобы не спрашивать заново)
                accepted       INTEGER NOT NULL DEFAULT 0,

                -- первый вход (UTC, ISO-8601)
                first_seen     TEXT,

                -- контактные данные (CDEK)
                cdek_fio       TEXT,
                cdek_phone     TEXT,
                cdek_address   TEXT,

                -- реквизиты для выплат
                payout_fio     TEXT,
                payout_card    TEXT,
                payout_bank    TEXT,

                -- устаревшие свободные поля (на случай совместимости)
                cdek_text      TEXT,
                payout_text    TEXT
            )
            """
        )

        # ---- requests: заявки пользователей ----
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                private_title TEXT NOT NULL,
                item_title    TEXT NOT NULL,
                description   TEXT NOT NULL,
                photo_file_id TEXT,
                status        TEXT NOT NULL DEFAULT 'pending',
                created_at    TEXT NOT NULL,
                moderated_at  TEXT,
                reject_reason TEXT
            )
            """
        )

        # ---- offers: отклики продавцов на заявки ----
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id    INTEGER NOT NULL,
                seller_id     INTEGER NOT NULL,
                price         REAL    NOT NULL,
                days          INTEGER NOT NULL,
                cond          INTEGER NOT NULL,   -- 1..10
                photo_file_id TEXT,
                created_at    TEXT    NOT NULL
            )
            """
        )

        # --- миграции ---
        await _migrate_user_profile(db)
        await _migrate_requests(db)
        await _migrate_offers(db)

        await db.commit()


async def _migrate_user_profile(db: aiosqlite.Connection) -> None:
    db.row_factory = aiosqlite.Row
    cur = await db.execute("PRAGMA table_info(user_profile)")
    cols = {row["name"] for row in await cur.fetchall()}

    if "accepted" not in cols:
        await db.execute("ALTER TABLE user_profile ADD COLUMN accepted INTEGER NOT NULL DEFAULT 0")
    if "first_seen" not in cols:
        await db.execute("ALTER TABLE user_profile ADD COLUMN first_seen TEXT")

    # новые структурированные поля (на случай очень старой схемы)
    for coldef in [
        ("cdek_fio", "TEXT"),
        ("cdek_phone", "TEXT"),
        ("cdek_address", "TEXT"),
        ("payout_fio", "TEXT"),
        ("payout_card", "TEXT"),
        ("payout_bank", "TEXT"),
        ("cdek_text", "TEXT"),
        ("payout_text", "TEXT"),
    ]:
        if coldef[0] not in cols:
            await db.execute(f"ALTER TABLE user_profile ADD COLUMN {coldef[0]} {coldef[1]}")


async def _migrate_requests(db: aiosqlite.Connection) -> None:
    db.row_factory = aiosqlite.Row
    cur = await db.execute("PRAGMA table_info(requests)")
    cols = {row["name"] for row in await cur.fetchall()}

    if "status" not in cols:
        await db.execute("ALTER TABLE requests ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
    if "moderated_at" not in cols:
        await db.execute("ALTER TABLE requests ADD COLUMN moderated_at TEXT")
    if "reject_reason" not in cols:
        await db.execute("ALTER TABLE requests ADD COLUMN reject_reason TEXT")

    # страховка на старых записях
    await db.execute("UPDATE requests SET status='pending' WHERE status IS NULL")


async def _migrate_offers(db: aiosqlite.Connection) -> None:
    db.row_factory = aiosqlite.Row
    cur = await db.execute("PRAGMA table_info(offers)")
    cols = {row["name"] for row in await cur.fetchall()}
    # Сейчас все колонки создаются сразу; блок ниже — страховка на будущее
    needed = {"id", "request_id", "seller_id", "price", "days", "cond", "photo_file_id", "created_at"}
    for c in needed - cols:
        if c == "photo_file_id":
            await db.execute("ALTER TABLE offers ADD COLUMN photo_file_id TEXT")
        elif c == "created_at":
            await db.execute("ALTER TABLE offers ADD COLUMN created_at TEXT")
        elif c == "cond":
            await db.execute("ALTER TABLE offers ADD COLUMN cond INTEGER NOT NULL DEFAULT 5")
        elif c == "price":
            await db.execute("ALTER TABLE offers ADD COLUMN price REAL NOT NULL DEFAULT 0")
        elif c == "days":
            await db.execute("ALTER TABLE offers ADD COLUMN days INTEGER NOT NULL DEFAULT 0")
        elif c == "request_id":
            await db.execute("ALTER TABLE offers ADD COLUMN request_id INTEGER NOT NULL DEFAULT 0")
        elif c == "seller_id":
            await db.execute("ALTER TABLE offers ADD COLUMN seller_id INTEGER NOT NULL DEFAULT 0")
