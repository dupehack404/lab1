# main.py — aiogram v3.x
# Полностью готовый файл под текущий config.py (все константы и БД — в config)

import asyncio
import aiosqlite
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, PhotoSize,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    InputMediaPhoto, FSInputFile
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from config import (
    # константы/пути/айди
    BOT_TOKEN, START_IMAGE_PATH, START_IMAGE_URL,
    MODERATION_CHAT_ID, PUBLISH_CHANNEL_ID,
    BUTTONS, ACCEPT_BUTTON_TEXT, ACCEPT_CALLBACK_DATA, WELCOME_TEXT,
    REPLY_BUTTONS,
    HELP_SUPPORT_USERNAME, HELP_NEWS_USERNAME, HELP_OFFERS_USERNAME, HELP_ADS_USERNAME,
    DB_PATH,
    # инициализация БД и миграции
    init_db
)

# ==== Routers ====
public_router = Router(name="public")
public_router.message.filter(F.chat.id != MODERATION_CHAT_ID)
public_router.callback_query.filter(F.message.chat.id != MODERATION_CHAT_ID)

mod_router = Router(name="moderation")
mod_router.message.filter(F.chat.id == MODERATION_CHAT_ID)
mod_router.callback_query.filter(F.message.chat.id == MODERATION_CHAT_ID)

# ===========================
# DB-утилиты поверх схемы из config.init_db
# ===========================
async def ensure_profile(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO user_profile (user_id) VALUES (?)", (user_id,))
        await db.execute(
            "UPDATE user_profile SET first_seen = COALESCE(first_seen, ?) WHERE user_id = ?",
            (datetime.utcnow().isoformat(), user_id)
        )
        await db.commit()

async def set_accepted(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_profile SET accepted=1 WHERE user_id=?", (user_id,))
        await db.commit()

async def is_user_accepted(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT accepted FROM user_profile WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return bool(row and row["accepted"])

async def get_profile(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM user_profile WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def insert_request(user_id: int, private_title: str, item_title: str,
                         description: str, photo_file_id: str | None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO requests (user_id, private_title, item_title, description, photo_file_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, private_title, item_title, description, photo_file_id, datetime.utcnow().isoformat())
        )
        await db.commit()
        return cur.lastrowid

async def list_user_requests_ordered(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM requests WHERE user_id=? ORDER BY id DESC",
            (user_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

async def count_user_requests(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM requests WHERE user_id=?", (user_id,))
        (n,) = await cur.fetchone()
        return int(n or 0)

async def get_request(req_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM requests WHERE id=?", (req_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def update_request_field(req_id: int, field: str, value: str | None) -> None:
    assert field in ("private_title", "item_title", "description", "photo_file_id")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE requests SET {field}=? WHERE id=?", (value, req_id))
        await db.commit()

async def update_request_status(req_id: int, status: str, reason: str | None = None) -> None:
    assert status in ("approved", "rejected")
    async with aiosqlite.connect(DB_PATH) as db:
        if status == "approved":
            await db.execute(
                "UPDATE requests SET status='approved', moderated_at=? WHERE id=?",
                (datetime.utcnow().isoformat(), req_id)
            )
        else:
            await db.execute(
                "UPDATE requests SET status='rejected', reject_reason=?, moderated_at=? WHERE id=?",
                (reason or "", datetime.utcnow().isoformat(), req_id)
            )
        await db.commit()

# ===== offers =====
async def insert_offer(request_id: int, seller_id: int, price: float,
                       days: int, cond: int, photo_file_id: str | None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO offers (request_id, seller_id, price, days, cond, photo_file_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (request_id, seller_id, price, days, cond, photo_file_id, datetime.utcnow().isoformat())
        )
        await db.commit()
        return cur.lastrowid

# ===========================
# Профиль: сохранение CDEK/реквизитов
# ===========================
async def save_cdek(user_id: int, fio: str, phone: str, address: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE user_profile
               SET cdek_fio=?, cdek_phone=?, cdek_address=?
             WHERE user_id=?
            """,
            (fio, phone, address, user_id)
        )
        await db.commit()

async def save_reqs(user_id: int, fio: str, card: str, bank: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE user_profile
               SET payout_fio=?, payout_card=?, payout_bank=?
             WHERE user_id=?
            """,
            (fio, card, bank, user_id)
        )
        await db.commit()

# ===========================
# Утилиты
# ===========================
def _cleanup(s: str | None) -> str:
    return (s or "").strip()

def largest_photo(photos: list[PhotoSize]) -> PhotoSize | None:
    return max(photos, key=lambda p: p.file_size or 0) if photos else None

# ===========================
# Клавиатуры
# ===========================
def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=BUTTONS[0]["text"], url=BUTTONS[0]["url"]),
        InlineKeyboardButton(text=BUTTONS[1]["text"], url=BUTTONS[1]["url"]),
    ], [
        InlineKeyboardButton(text=ACCEPT_BUTTON_TEXT, callback_data=ACCEPT_CALLBACK_DATA)
    ]])

def menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=REPLY_BUTTONS[0]),
                   KeyboardButton(text=REPLY_BUTTONS[1]),
                   KeyboardButton(text=REPLY_BUTTONS[2])]],
        resize_keyboard=True
    )

def requests_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Активные запросы")],
            [KeyboardButton(text="Создать новый запрос")],
            [KeyboardButton(text="Вернуться")]
        ],
        resize_keyboard=True
    )

def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Тех. Поддержка",        url=f"https://t.me/{HELP_SUPPORT_USERNAME}")],
        [InlineKeyboardButton(text="🔘 Канал",                 url=f"https://t.me/{HELP_NEWS_USERNAME}")],
        [InlineKeyboardButton(text="🔘 Канал с заявками",      url=f"https://t.me/{HELP_OFFERS_USERNAME}")],
        [InlineKeyboardButton(text="🔘 Реклама/предложения",   url=f"https://t.me/{HELP_ADS_USERNAME}")],
    ])

# Профиль
CB_PROFILE_CDEK = "profile:cdek"
CB_PROFILE_REQS = "profile:reqs"
CB_PROFILE_BACK = "profile:back"

def profile_missing_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Контактные данные (CDEK)", callback_data=CB_PROFILE_CDEK)],
        [InlineKeyboardButton(text="Реквизиты",                 callback_data=CB_PROFILE_REQS)],
    ])

def back_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Вернуться", callback_data=CB_PROFILE_BACK)]
    ])

# Слайдер активных заявок
def slider_kb(idx: int, total: int, req_id: int) -> InlineKeyboardMarkup:
    rows = []
    if total > 1:
        nav_row = []
        if idx > 0:
            nav_row.append(InlineKeyboardButton(text="◀︎", callback_data=f"rl:go:{idx-1}"))
        if idx < total - 1:
            nav_row.append(InlineKeyboardButton(text="▶︎", callback_data=f"rl:go:{idx+1}"))
        if nav_row:
            rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="🔘 Изменить запрос", callback_data=f"rl:edit:{req_id}")])
    rows.append([InlineKeyboardButton(text="🔘 Вернуться", callback_data="rl:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def change_existing_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Личное название", callback_data="re:ep")],
        [InlineKeyboardButton(text="🔘 Название",        callback_data="re:ei")],
        [InlineKeyboardButton(text="🔘 Описание",        callback_data="re:ed")],
        [InlineKeyboardButton(text="🔘 Фото",            callback_data="re:ph")],
        [InlineKeyboardButton(text="↩️ Вернуться к заявке", callback_data="re:back")]
    ])

# Создание заявки — клавиатуры
CB_REQ_SKIP_PHOTO = "req:skip_photo"
CB_REQ_CONFIRM    = "req:confirm"
CB_REQ_CHANGE     = "req:change"

def photo_or_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Пропустить", callback_data=CB_REQ_SKIP_PHOTO)]
    ])

def confirm_or_change_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔘 Подтвердить", callback_data=CB_REQ_CONFIRM),
         InlineKeyboardButton(text="🔘 Изменить",    callback_data=CB_REQ_CHANGE)]
    ])

# Админ-модерация
def admin_moderation_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить",  callback_data=f"adm:ok:{req_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm:rej:{req_id}")]
    ])

# Публичный пост в канал
def build_public_post_text(row: dict) -> str:
    return (
        f"🧾 Заявка №{row['id']}\n"
        f"• Название: {row.get('item_title') or '—'}\n"
        f"• Описание: {row.get('description') or '—'}"
    )

def public_offer_kb(bot_username: str, req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Откликнуться",
            url=f"https://t.me/{bot_username}?start=offer_{req_id}"
        )
    ]])

# Отклик: клавиатуры
def offer_condition_kb() -> InlineKeyboardMarkup:
    rows = []
    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"offer:cond:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"offer:cond:{i}") for i in range(6, 11)]
    rows.append(row1)
    rows.append(row2)
    return InlineKeyboardMarkup(inline_keyboard=rows)

CB_OFFER_SKIP_PHOTO = "offer:skip_photo"

# ===========================
# Тексты профиля и форматирование
# ===========================
TEXT_CDEK_PROMPT = (
    "⬇️ Контактные данные (CDEK)\n"
    "Отправьте одним сообщением:\n"
    "1) ФИО\n2) Номер телефона\n3) Адрес пункта выдачи CDEK"
)
TEXT_REQS_PROMPT = (
    "⬇️ Реквизиты для выплат\n"
    "Отправьте одним сообщением:\n"
    "1) ФИО\n2) Номер карты (16 цифр)\n3) Банк"
)

def fmt_cdek(profile: dict) -> str:
    fio = profile.get("cdek_fio") or "—"
    phone = profile.get("cdek_phone") or "—"
    addr = profile.get("cdek_address") or "—"
    return (f"• Контактные данные (CDEK)\n"
            f"  1) ФИО: {fio}\n"
            f"  2) Телефон: {phone}\n"
            f"  3) Адрес ПВЗ: {addr}")

def fmt_reqs(profile: dict) -> str:
    fio = profile.get("payout_fio") or "—"
    card = profile.get("payout_card") or "—"
    bank = profile.get("payout_bank") or "—"
    return (f"• Реквизиты\n"
            f"  1) ФИО: {fio}\n"
            f"  2) Карта: {card}\n"
            f"  3) Банк: {bank}")

def has_cdek(profile: dict | None) -> bool:
    return bool(profile and (profile.get("cdek_fio") or profile.get("cdek_phone") or profile.get("cdek_address")))

def has_reqs(profile: dict | None) -> bool:
    return bool(profile and (profile.get("payout_fio") or profile.get("payout_card") or profile.get("payout_bank")))

async def build_profile_stats_text(user_id: int) -> str:
    profile = await get_profile(user_id)
    first_seen = profile.get("first_seen") if profile else None
    dt = None
    if first_seen:
        try:
            dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        except Exception:
            dt = None
    date_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"
    total_requests = await count_user_requests(user_id)
    successful_offers = 0
    total_deals_sum = 0.0
    return (
        f"Профиль ({user_id})\n"
        f"• Дата регистрации в боте «первый вход»: {date_str}\n"
        f"• Количество размещенных заявок: {total_requests}\n"
        f"• Количество успешных откликов на заказы пользователей: {successful_offers}\n"
        f"• Сумма всех сделок: {total_deals_sum}\n"
        f"• Внесены контактные данные: {'Да' if has_cdek(profile) else 'Нет'}\n"
        f"• Внесены реквизиты: {'Да' if has_reqs(profile) else 'Нет'}"
    )

# ===========================
# FSM
# ===========================
class ProfileFill(StatesGroup):
    wait_cdek = State()
    wait_reqs = State()

class AdminReject(StatesGroup):
    waiting_reason = State()

class OfferCreate(StatesGroup):
    wait_price     = State()
    wait_days      = State()
    wait_condition = State()
    wait_photo     = State()

class RequestCreate(StatesGroup):
    wait_private_title = State()
    wait_item_title    = State()
    wait_description   = State()
    wait_photo         = State()

# ===========================
# Контекст для слайдера
# ===========================
REQ_PAGES: dict[int, list[int]] = {}

# ===========================
# Служебные рендеры
# ===========================
def request_preview_text(row: dict) -> str:
    return (
        f"№{row['id']} — {row.get('item_title', '—')} ({row.get('status', '—')})\n\n"
        f"Личное название: {row.get('private_title') or '—'}\n"
        f"Описание: {row.get('description') or '—'}"
    )

async def show_request_slide(cbq_or_msg, row: dict, idx: int, total: int):
    kb = slider_kb(idx, total, row["id"])
    caption = request_preview_text(row)
    photo_id = row.get("photo_file_id")

    if isinstance(cbq_or_msg, CallbackQuery):
        msg = cbq_or_msg.message
        if photo_id:
            try:
                await msg.edit_media(InputMediaPhoto(media=photo_id, caption=caption), reply_markup=kb)
                return
            except Exception as e:
                print("edit_media failed:", e)
        try:
            await msg.edit_text(caption, reply_markup=kb)
            return
        except Exception as e:
            print("edit_text failed:", e)
            if photo_id:
                await msg.answer_photo(photo_id, caption=caption, reply_markup=kb)
            else:
                await msg.answer(caption, reply_markup=kb)
            try:
                await msg.delete()
            except Exception as e2:
                print("delete old slide failed:", e2)
            return
    else:
        if photo_id:
            await cbq_or_msg.answer_photo(photo_id, caption=caption, reply_markup=kb)
        else:
            await cbq_or_msg.answer(caption, reply_markup=kb)

def draft_preview_text(d: dict) -> str:
    return (
        "«Предпросмотр заявки»\n\n"
        f"• Личное название: {d.get('draft_private_title') or '—'}\n"
        f"• Название: {d.get('draft_item_title') or '—'}\n"
        f"• Описание:\n{d.get('draft_description') or '—'}"
    )

async def show_draft_preview(message_or_cbq, state: FSMContext) -> None:
    data = await state.get_data()
    caption = draft_preview_text(data)
    kb = confirm_or_change_kb()
    photo_id = data.get("draft_photo_file_id")
    if isinstance(message_or_cbq, CallbackQuery):
        msg = message_or_cbq.message
        if photo_id:
            try:
                await msg.edit_media(InputMediaPhoto(media=photo_id, caption=caption), reply_markup=kb)
                return
            except Exception:
                pass
        try:
            await msg.edit_text(caption, reply_markup=kb)
        except Exception:
            if photo_id:
                await msg.answer_photo(photo_id, caption=caption, reply_markup=kb)
            else:
                await msg.answer(caption, reply_markup=kb)
    else:
        if photo_id:
            await message_or_cbq.answer_photo(photo_id, caption=caption, reply_markup=kb)
        else:
            await message_or_cbq.answer(caption, reply_markup=kb)

async def ensure_access_or_prompt(message: Message) -> bool:
    uid = message.from_user.id
    if await is_user_accepted(uid):
        return True
    await message.answer(
        "Перед началом использования сервиса просьба ознакомиться с нашей публичной офертой.",
        reply_markup=start_keyboard()
    )
    return False

# ===========================
# Уведомление в модерацию
# ===========================
async def notify_admin_group(bot: Bot, row: dict, author_id: int) -> None:
    text = (
        "🆕 Новая заявка на модерацию\n"
        f"№{row['id']} (от user_id={author_id})\n\n"
        f"• Личное название: {row.get('private_title') or '—'}\n"
        f"• Название вещи: {row.get('item_title') or '—'}\n"
        f"• Описание: {row.get('description') or '—'}\n"
        f"• Фото: {'есть ✅' if row.get('photo_file_id') else 'нет'}\n"
        f"• Статус: {row.get('status')}\n"
    )
    kb = admin_moderation_kb(row["id"])
    if row.get("photo_file_id"):
        await bot.send_photo(MODERATION_CHAT_ID, row["photo_file_id"], caption=text, reply_markup=kb)
    else:
        await bot.send_message(MODERATION_CHAT_ID, text, reply_markup=kb)

# ===========================
# /start (+ deep-link offer_<id>) — оферта один раз
# ===========================
@public_router.message(F.text.startswith("/start"))
async def on_start(message: Message, state: FSMContext) -> None:
    await ensure_profile(message.from_user.id)

    parts = (message.text or "").split(maxsplit=1)
    # deep-link: /start offer_123
    if len(parts) > 1 and parts[1].startswith("offer_"):
        payload = parts[1]
        try:
            req_id = int(payload.split("_", 1)[1])
        except Exception:
            await message.answer("Некорректный стартовый параметр.")
            return

        req = await get_request(req_id)
        if not req:
            await message.answer(f"Заявка №{req_id} не найдена.")
            return

        await state.set_state(OfferCreate.wait_price)
        await state.update_data(offer_req_id=req_id)
        await message.answer(
            f"Отлично! Введите цену, за которую вы готовы привезти заказ №{req_id} "
            "(учитывайте товар, логистику до вашего города и до Москвы, а также наценку)."
        )
        return

    # обычный /start (с офертой/картинкой)
    if await is_user_accepted(message.from_user.id):
        await message.answer(WELCOME_TEXT, reply_markup=menu_keyboard())
        return

    caption = "Перед началом использования сервиса просьба ознакомиться с нашей публичной офертой."

    # 1) локальный файл
    img_path = Path(START_IMAGE_PATH).expanduser().resolve()
    if img_path.exists() and img_path.is_file():
        try:
            await message.answer_photo(FSInputFile(img_path), caption=caption, reply_markup=start_keyboard())
            return
        except Exception as e:
            print(f"[start-image] local send failed: {e}")

    # 2) URL
    if START_IMAGE_URL:
        try:
            await message.answer_photo(START_IMAGE_URL, caption=caption, reply_markup=start_keyboard())
            return
        except Exception as e:
            print(f"[start-image] url send failed: {e}")

    # 3) fallback
    await message.answer(caption, reply_markup=start_keyboard())

@public_router.callback_query(F.data == ACCEPT_CALLBACK_DATA)
async def on_accept(cbq: CallbackQuery) -> None:
    await set_accepted(cbq.from_user.id)  # запоминаем, больше не спросим
    await cbq.message.answer(WELCOME_TEXT, reply_markup=menu_keyboard())
    await cbq.answer("Доступ открыт.")

# ===========================
# ПОМОЩЬ
# ===========================
@public_router.message(F.text == "Помощь")
async def on_help(message: Message) -> None:
    if not await ensure_access_or_prompt(message):
        return
    await message.answer("Выберите нужный раздел ниже:", reply_markup=help_keyboard())

# ===========================
# ПРОФИЛЬ
# ===========================
@public_router.message(F.text == "Мой профиль")
async def on_profile(message: Message) -> None:
    if not await ensure_access_or_prompt(message):
        return
    await ensure_profile(message.from_user.id)

    stats_text = await build_profile_stats_text(message.from_user.id)
    await message.answer(stats_text)

    profile = await get_profile(message.from_user.id)
    if not has_cdek(profile) or not has_reqs(profile):
        await message.answer(
            "Для заказов желательно заполнить контактные данные и реквизиты.",
            reply_markup=profile_missing_keyboard()
        )
    else:
        await message.answer(
            f"{fmt_cdek(profile)}\n\n{fmt_reqs(profile)}",
            reply_markup=profile_missing_keyboard()
        )

@public_router.callback_query(F.data == CB_PROFILE_CDEK)
async def on_profile_cdek(cbq: CallbackQuery, state: FSMContext) -> None:
    await ensure_profile(cbq.from_user.id)
    profile = await get_profile(cbq.from_user.id)

    if has_cdek(profile):
        await cbq.message.answer(
            f"{fmt_cdek(profile)}\n\n"
            "Чтобы обновить — отправьте одним сообщением:\n"
            "1) ФИО\n2) Телефон\n3) Адрес ПВЗ CDEK",
            reply_markup=back_inline_keyboard()
        )
    else:
        await cbq.message.answer(TEXT_CDEK_PROMPT, reply_markup=back_inline_keyboard())

    await state.set_state(ProfileFill.wait_cdek)
    await cbq.answer()

@public_router.callback_query(F.data == CB_PROFILE_REQS)
async def on_profile_reqs(cbq: CallbackQuery, state: FSMContext) -> None:
    await ensure_profile(cbq.from_user.id)
    profile = await get_profile(cbq.from_user.id)

    if has_reqs(profile):
        await cbq.message.answer(
            f"{fmt_reqs(profile)}\n\n"
            "Чтобы обновить — отправьте одним сообщением:\n"
            "1) ФИО\n2) Номер карты (16 цифр)\n3) Банк",
            reply_markup=back_inline_keyboard()
        )
    else:
        await cbq.message.answer(TEXT_REQS_PROMPT, reply_markup=back_inline_keyboard())

    await state.set_state(ProfileFill.wait_reqs)
    await cbq.answer()

@public_router.callback_query(F.data == CB_PROFILE_BACK)
async def on_profile_back(cbq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    stats_text = await build_profile_stats_text(cbq.from_user.id)
    await cbq.message.answer(stats_text)
    profile = await get_profile(cbq.from_user.id)
    if not has_cdek(profile) or not has_reqs(profile):
        await cbq.message.answer(
            "Для заказов желательно заполнить контактные данные и реквизиты.",
            reply_markup=profile_missing_keyboard()
        )
    else:
        await cbq.message.answer(
            f"{fmt_cdek(profile)}\n\n{fmt_reqs(profile)}",
            reply_markup=profile_missing_keyboard()
        )
    await cbq.answer("Возврат без изменений.")

@public_router.message(ProfileFill.wait_cdek)
async def on_cdek_fill(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("\n") if p.strip()]
    if len(parts) < 3:
        await message.answer("Нужно прислать 3 строки: ФИО, телефон, адрес ПВЗ. Отправьте ещё раз.")
        return

    fio, phone, address = parts[0], parts[1], "\n".join(parts[2:])
    await save_cdek(message.from_user.id, fio, phone, address)
    await state.clear()

    profile = await get_profile(message.from_user.id)
    await message.answer("Контактные данные сохранены ✅")
    await message.answer(f"{fmt_cdek(profile)}", reply_markup=profile_missing_keyboard())

@public_router.message(ProfileFill.wait_reqs)
async def on_reqs_fill(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("\n") if p.strip()]
    if len(parts) < 3:
        await message.answer("Нужно прислать 3 строки: ФИО, номер карты, банк. Отправьте ещё раз.")
        return

    fio, card, bank = parts[0], parts[1], "\n".join(parts[2:])
    await save_reqs(message.from_user.id, fio, card, bank)
    await state.clear()

    profile = await get_profile(message.from_user.id)
    await message.answer("Реквизиты сохранены ✅")
    await message.answer(f"{fmt_reqs(profile)}", reply_markup=profile_missing_keyboard())

# ===========================
# МОИ ЗАПРОСЫ / СЛАЙДЕР
# ===========================
@public_router.message(F.text == "Мои запросы")
async def on_requests_menu(message: Message) -> None:
    if not await ensure_access_or_prompt(message):
        return
    await message.answer("Раздел «Мои запросы».", reply_markup=requests_keyboard())

@public_router.message(F.text == "Активные запросы")
async def on_active_requests(message: Message) -> None:
    if not await ensure_access_or_prompt(message):
        return
    rows = await list_user_requests_ordered(message.from_user.id)
    if not rows:
        await message.answer("Пока активных запросов нет.", reply_markup=requests_keyboard()); return
    REQ_PAGES[message.from_user.id] = [r["id"] for r in rows]
    await show_request_slide(message, rows[0], idx=0, total=len(rows))

@public_router.callback_query(F.data.startswith("rl:go:"))
async def on_slider_go(cbq: CallbackQuery) -> None:
    uid = cbq.from_user.id
    ids = REQ_PAGES.get(uid)
    if not ids:
        await cbq.answer("Нет списка заявок."); return
    try:
        idx = int(cbq.data.split(":")[-1])
    except Exception:
        await cbq.answer(); return
    if not (0 <= idx < len(ids)):
        await cbq.answer(); return
    row = await get_request(ids[idx])
    if not row:
        await cbq.answer("Заявка не найдена."); return
    await show_request_slide(cbq, row, idx=idx, total=len(ids))
    await cbq.answer()

@public_router.callback_query(F.data == "rl:back")
async def on_slider_back(cbq: CallbackQuery) -> None:
    await cbq.message.answer("Раздел «Мои запросы».", reply_markup=requests_keyboard())
    await cbq.answer()

@public_router.callback_query(F.data.startswith("rl:edit:"))
async def on_slider_edit(cbq: CallbackQuery, state: FSMContext) -> None:
    try:
        req_id = int(cbq.data.split(":")[-1])
    except Exception:
        await cbq.answer("Некорректные данные.", show_alert=True); return
    await state.update_data(edit_req_id=req_id)
    await cbq.message.answer("Что конкретно вы хотите изменить?", reply_markup=change_existing_kb())
    await cbq.answer()

@public_router.callback_query(F.data == "re:back")
async def on_edit_back(cbq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    req_id = data.get("edit_req_id")
    if not req_id:
        await cbq.answer("Нет контекста.", show_alert=True); return
    # вернёмся к карточке
    uid = cbq.from_user.id
    ids = REQ_PAGES.get(uid) or [req_id]
    try:
        idx = ids.index(req_id)
    except Exception:
        idx = 0
    row = await get_request(req_id)
    if not row:
        await cbq.answer("Заявка не найдена.", show_alert=True); return
    await show_request_slide(cbq, row, idx=idx, total=len(ids))
    await cbq.answer("Возврат к заявке.")

# Примеры упрощённых обработчиков изменения полей (без FSM на каждое поле)
@public_router.callback_query(F.data == "re:ep")
async def on_edit_private_title(cbq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RequestCreate.wait_private_title)
    await cbq.message.answer("Введите новое личное название.")
    await cbq.answer()

@public_router.callback_query(F.data == "re:ei")
async def on_edit_item_title(cbq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RequestCreate.wait_item_title)
    await cbq.message.answer("Введите новое полное название вещи.")
    await cbq.answer()

@public_router.callback_query(F.data == "re:ed")
async def on_edit_description(cbq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RequestCreate.wait_description)
    await cbq.message.answer("Введите новое описание.")
    await cbq.answer()

@public_router.callback_query(F.data == "re:ph")
async def on_edit_photo(cbq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RequestCreate.wait_photo)
    await cbq.message.answer("Прикрепите новое фото или нажмите «Пропустить».", reply_markup=photo_or_skip_kb())
    await cbq.answer()

# ===========================
# СОЗДАНИЕ НОВОГО ЗАПРОСА (мастер)
# ===========================
@public_router.message(F.text == "Создать новый запрос")
async def req_new_start(message: Message, state: FSMContext) -> None:
    if not await ensure_access_or_prompt(message):
        return
    await state.clear()
    await state.set_state(RequestCreate.wait_private_title)
    await message.answer("Укажите название запроса. Оно будет видно только вам.")

@public_router.message(RequestCreate.wait_private_title)
async def req_private_title(message: Message, state: FSMContext) -> None:
    title = _cleanup(message.text)
    if not title:
        await message.answer("Пустое название. Введите ещё раз.")
        return

    # Если редактируем существующую заявку
    data = await state.get_data()
    edit_id = data.get("edit_req_id")
    if edit_id:
        await update_request_field(edit_id, "private_title", title)
        await state.clear()
        row = await get_request(edit_id)
        await message.answer("Личное название обновлено ✅")
        # показать обновлённую карточку
        uid = message.from_user.id
        ids = REQ_PAGES.get(uid) or [edit_id]
        try:
            idx = ids.index(edit_id)
        except Exception:
            idx = 0
        await show_request_slide(message, row, idx=idx, total=len(ids))
        return

    await state.update_data(draft_private_title=title)
    await state.set_state(RequestCreate.wait_item_title)
    await message.answer("Отлично, теперь пришлите полное название вещи.")

@public_router.message(RequestCreate.wait_item_title)
async def req_item_title(message: Message, state: FSMContext) -> None:
    item = _cleanup(message.text)
    if not item:
        await message.answer("Пустое название. Введите ещё раз.")
        return

    data = await state.get_data()
    edit_id = data.get("edit_req_id")
    if edit_id:
        await update_request_field(edit_id, "item_title", item)
        await state.clear()
        row = await get_request(edit_id)
        await message.answer("Название обновлено ✅")
        uid = message.from_user.id
        ids = REQ_PAGES.get(uid) or [edit_id]
        try:
            idx = ids.index(edit_id)
        except Exception:
            idx = 0
        await show_request_slide(message, row, idx=idx, total=len(ids))
        return

    await state.update_data(draft_item_title=item)
    await state.set_state(RequestCreate.wait_description)
    await message.answer("Отправьте пожалуйста описание с интересующим цветом, состоянием, размером и остальными деталями.")

@public_router.message(RequestCreate.wait_description)
async def req_description(message: Message, state: FSMContext) -> None:
    desc = _cleanup(message.text)
    if not desc:
        await message.answer("Пустое описание. Введите ещё раз.")
        return

    data = await state.get_data()
    edit_id = data.get("edit_req_id")
    if edit_id:
        await update_request_field(edit_id, "description", desc)
        await state.clear()
        row = await get_request(edit_id)
        await message.answer("Описание обновлено ✅")
        uid = message.from_user.id
        ids = REQ_PAGES.get(uid) or [edit_id]
        try:
            idx = ids.index(edit_id)
        except Exception:
            idx = 0
        await show_request_slide(message, row, idx=idx, total=len(ids))
        return

    await state.update_data(draft_description=desc)
    await state.set_state(RequestCreate.wait_photo)
    await message.answer(
        "Замечательно, если есть фото вещи, можете прикрепить его или же пропустить этот этап.",
        reply_markup=photo_or_skip_kb()
    )

@public_router.message(RequestCreate.wait_photo, F.photo)
async def req_take_photo(message: Message, state: FSMContext) -> None:
    ph = largest_photo(message.photo)
    if ph:
        data = await state.get_data()
        edit_id = data.get("edit_req_id")
        if edit_id:
            await update_request_field(edit_id, "photo_file_id", ph.file_id)
            await state.clear()
            row = await get_request(edit_id)
            await message.answer("Фото обновлено ✅")
            uid = message.from_user.id
            ids = REQ_PAGES.get(uid) or [edit_id]
            try:
                idx = ids.index(edit_id)
            except Exception:
                idx = 0
            await show_request_slide(message, row, idx=idx, total=len(ids))
            return

        await state.update_data(draft_photo_file_id=ph.file_id)
    await show_draft_preview(message, state)

@public_router.callback_query(F.data == CB_REQ_SKIP_PHOTO)
async def req_skip_photo(cbq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(draft_photo_file_id=None)
    await show_draft_preview(cbq, state)
    await cbq.answer("Фото пропущено.")

@public_router.callback_query(F.data == CB_REQ_CONFIRM)
async def req_confirm(cbq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pt  = _cleanup(data.get("draft_private_title"))
    it  = _cleanup(data.get("draft_item_title"))
    ds  = _cleanup(data.get("draft_description"))
    ph  = data.get("draft_photo_file_id")

    if not (pt and it and ds):
        await cbq.answer("Не все поля заполнены.", show_alert=True)
        return

    new_id = await insert_request(cbq.from_user.id, pt, it, ds, ph)
    row = await get_request(new_id)

    # уведомляем модераторскую беседу
    await notify_admin_group(cbq.bot, row, cbq.from_user.id)

    await state.clear()
    await cbq.message.answer("Отлично! Ваша заявка отправлена на модерацию, вам придет уведомление когда она будет опубликована. ♻️")
    await cbq.answer("Отправлено на модерацию ✅")

@public_router.callback_query(F.data == CB_REQ_CHANGE)
async def req_change(cbq: CallbackQuery, state: FSMContext) -> None:
    await cbq.message.answer("Что конкретно вы хотите изменить?", reply_markup=change_existing_kb())
    await cbq.answer()

# ===========================
# ОТКЛИК НА ЗАЯВКУ: цена → дни → состояние → фото/Пропустить
# ===========================
@public_router.message(OfferCreate.wait_price)
async def offer_step_price(message: Message, state: FSMContext) -> None:
    txt = _cleanup(message.text)
    try:
        price = float(txt.replace(",", "."))
        if price <= 0:
            raise ValueError
    except Exception:
        await message.answer("Введите число — цену (например, 12500.00).")
        return

    await state.update_data(offer_price=price)
    await state.set_state(OfferCreate.wait_days)
    await message.answer("Введите количество дней доставки до прибытия в пункт выдачи Модератора Someout.")

@public_router.message(OfferCreate.wait_days)
async def offer_step_days(message: Message, state: FSMContext) -> None:
    txt = _cleanup(message.text)
    try:
        days = int(txt)
        if days <= 0 or days > 365:
            raise ValueError
    except Exception:
        await message.answer("Введите целое число дней (1..365).")
        return

    await state.update_data(offer_days=days)
    await state.set_state(OfferCreate.wait_condition)
    await message.answer(
        "Выберите состояние предмета по шкале:\n1 — Ужасное … 10 — Новое с биркой",
        reply_markup=offer_condition_kb()
    )

@public_router.callback_query(F.data.startswith("offer:cond:"))
async def offer_pick_condition(cbq: CallbackQuery, state: FSMContext) -> None:
    try:
        cond = int(cbq.data.split(":")[-1])
        if not (1 <= cond <= 10):
            raise ValueError
    except Exception:
        await cbq.answer("Некорректное значение.", show_alert=True); 
        return

    await state.update_data(offer_cond=cond)
    await state.set_state(OfferCreate.wait_photo)
    await cbq.message.answer(
        "Если у вас есть фото товара — прикрепите его одним сообщением.\nИли нажмите «Пропустить».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔘 Пропустить", callback_data=CB_OFFER_SKIP_PHOTO)]
        ])
    )
    await cbq.answer()

@public_router.message(OfferCreate.wait_photo, F.photo)
async def offer_take_photo(message: Message, state: FSMContext) -> None:
    ph = largest_photo(message.photo)
    photo_id = ph.file_id if ph else None
    await _finalize_offer(message, state, photo_id)

@public_router.callback_query(F.data == CB_OFFER_SKIP_PHOTO)
async def offer_skip_photo(cbq: CallbackQuery, state: FSMContext) -> None:
    await _finalize_offer(cbq, state, photo_id=None)
    await cbq.answer("Отправлено без фото.")

async def _finalize_offer(cbq_or_msg, state: FSMContext, photo_id: str | None) -> None:
    data = await state.get_data()
    req_id   = data.get("offer_req_id")
    price    = data.get("offer_price")
    days     = data.get("offer_days")
    cond     = data.get("offer_cond")
    seller   = cbq_or_msg.from_user.id

    if not all([req_id, price is not None, days is not None, cond is not None]):
        await state.clear()
        msg = cbq_or_msg.message if isinstance(cbq_or_msg, CallbackQuery) else cbq_or_msg
        await msg.answer("Контекст отклика утерян. Повторите переход по кнопке «Откликнуться».")
        return

    offer_id = await insert_offer(int(req_id), seller, float(price), int(days), int(cond), photo_id)

    summary = (
        f"✅ Отклик отправлен (№{offer_id})\n"
        f"• Заявка №{req_id}\n"
        f"• Цена: {price}\n"
        f"• Срок: {days} дн.\n"
        f"• Состояние: {cond}/10\n"
        f"• Фото: {'есть' if photo_id else 'нет'}"
    )

    if isinstance(cbq_or_msg, CallbackQuery):
        msg = cbq_or_msg.message
        if photo_id:
            await msg.answer_photo(photo_id, caption=summary)
        else:
            await msg.answer(summary)
    else:
        if photo_id:
            await cbq_or_msg.answer_photo(photo_id, caption=summary)
        else:
            await cbq_or_msg.answer(summary)

    # Уведомим автора заявки
    try:
        req = await get_request(int(req_id))
        if req:
            text_for_author = (
                f"🙋 На вашу заявку №{req_id} пришёл отклик!\n"
                f"Цена: {price}\nСроки: {days} дн.\nСостояние: {cond}/10"
            )
            if photo_id:
                await cbq_or_msg.bot.send_photo(req["user_id"], photo_id, caption=text_for_author)
            else:
                await cbq_or_msg.bot.send_message(req["user_id"], text_for_author)
    except Exception as e:
        print("notify author warn:", e)

    await state.clear()

# ===========================
# МОДЕРАЦИЯ (approve / reject)
# ===========================
@mod_router.callback_query(F.data.startswith("adm:ok:"))
async def admin_approve(cbq: CallbackQuery) -> None:
    try:
        req_id = int(cbq.data.split(":")[-1])
    except Exception:
        await cbq.answer("Некорректные данные.", show_alert=True); return

    row = await get_request(req_id)
    if not row:
        await cbq.answer("Заявка не найдена.", show_alert=True); return
    if row["status"] != "pending":
        await cbq.answer("Заявка уже промодерирована."); return

    await update_request_status(req_id, "approved")

    # уведомляем автора
    try:
        await cbq.bot.send_message(row["user_id"], f"✅ Заявка №{req_id} прошла модерацию и уже выставлена в канал.")
    except Exception as e:
        print("warn DM:", e)

    # публикация в канал
    try:
        me = await cbq.bot.get_me()
        text = build_public_post_text(row)
        kb = public_offer_kb(me.username, req_id)
        if row.get("photo_file_id"):
            await cbq.bot.send_photo(PUBLISH_CHANNEL_ID, row["photo_file_id"], caption=text, reply_markup=kb)
        else:
            await cbq.bot.send_message(PUBLISH_CHANNEL_ID, text, reply_markup=kb)
    except Exception as e:
        print("publish error:", e)
        await cbq.answer("Нет прав публиковать в канал.", show_alert=True); return

    # убираем кнопки под карточкой в модерации
    try:
        row2 = await get_request(req_id)
        t = request_preview_text(row2)
        if row2.get("photo_file_id"):
            await cbq.message.edit_media(InputMediaPhoto(media=row2["photo_file_id"], caption=t))
        else:
            await cbq.message.edit_text(t)
        await cbq.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        print("edit moderation msg:", e)

    await cbq.answer("Одобрено и опубликовано ✅")

@mod_router.callback_query(F.data.startswith("adm:rej:"))
async def admin_reject_start(cbq: CallbackQuery, state: FSMContext) -> None:
    try:
        req_id = int(cbq.data.split(":")[-1])
    except Exception:
        await cbq.answer("Некорректные данные.", show_alert=True); return

    row = await get_request(req_id)
    if not row or row["status"] != "pending":
        await cbq.answer("Заявка не найдена/уже промодерирована.", show_alert=True); return

    await state.set_state(AdminReject.waiting_reason)
    await state.update_data(reject_req_id=req_id, admin_msg_id=cbq.message.message_id, admin_chat_id=cbq.message.chat.id)
    await cbq.message.answer("Укажите причину отклонения (одним сообщением).")
    await cbq.answer("Жду причину…")

@mod_router.message(AdminReject.waiting_reason)
async def admin_reject_reason(message: Message, state: FSMContext) -> None:
    if message.chat.id != MODERATION_CHAT_ID:
        return

    data = await state.get_data()
    req_id = data.get("reject_req_id")
    if not req_id:
        await state.clear()
        await message.answer("Контекст отклонения потерян. Нажмите «Отклонить» ещё раз.")
        return

    reason = _cleanup(message.text)
    if not reason:
        await message.answer("Причина не может быть пустой. Напишите текст причины."); return

    await update_request_status(req_id, "rejected", reason)
    row = await get_request(req_id)

    try:
        await message.bot.send_message(
            row["user_id"],
            f"❌ Ваша заявка №{req_id} не прошла модерацию. Причина: {reason}\n"
            "Пожалуйста внесите изменения и отправьте на повторную проверку."
        )
    except Exception as e:
        print("warn DM reject:", e)

    try:
        t = request_preview_text(row)
        if row.get("photo_file_id"):
            await message.bot.edit_message_media(
                chat_id=data["admin_chat_id"],
                message_id=data["admin_msg_id"],
                media=InputMediaPhoto(media=row["photo_file_id"], caption=t),
                reply_markup=None
            )
        else:
            await message.bot.edit_message_text(
                chat_id=data["admin_chat_id"],
                message_id=data["admin_msg_id"],
                text=t,
                reply_markup=None
            )
    except Exception as e:
        print("edit moderation msg (reject):", e)

    await state.clear()
    await message.answer("Отклонено ❌")

# ===========================
# Навигация/фолбек
# ===========================
@public_router.message(F.text == "Вернуться")
async def on_back_to_main_menu(message: Message) -> None:
    if not await ensure_access_or_prompt(message):
        return
    await message.answer("Главное меню.", reply_markup=menu_keyboard())

@public_router.message()
async def any_message(message: Message) -> None:
    if not await ensure_access_or_prompt(message):
        return
    await message.answer("Команда принята.", reply_markup=menu_keyboard())

# ===========================
# Entry Point
# ===========================
async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Укажи токен в config.py -> BOT_TOKEN")

    # создаём/мигрируем БД (всё внутри config.init_db)
    await init_db()

    # предупреждение, если не найдено стартовое изображение
    p = Path(START_IMAGE_PATH).expanduser().resolve()
    if not p.exists() and not START_IMAGE_URL:
        print(f"[info] Стартовое изображение не задано: {p} и START_IMAGE_URL пуст. Будет использован текст без фото.")

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(public_router)
    dp.include_router(mod_router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
