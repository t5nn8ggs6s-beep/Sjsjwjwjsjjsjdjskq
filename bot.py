import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict
import json
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, PreCheckoutQuery, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite

# Конфигурация
BOT_TOKEN = "8795245479:AAEJDUaWQuyekjxdLRWPapCL0tgzJzuiDks"  # Замените на ваш токен
ADMIN_IDS = [1541550837]  # ID создателя бота
CURRENCY = "XTR"  # Звёзды Telegram

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния для FSM
class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_user_id = State()
    waiting_for_balance_amount = State()
    waiting_for_course_change = State()
    waiting_for_price_change = State()
    waiting_for_duration_change = State()
    waiting_for_admin_add = State()
    waiting_for_admin_remove = State()
    waiting_for_course_give = State()
    waiting_for_course_give_duration = State()

# Класс для работы с базой данных
class Database:
    def __init__(self, db_path="telekiness_bot.db"):
        self.db_path = db_path
    
    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Таблица пользователей
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    balance INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    registered_date TEXT,
                    last_activity TEXT
                )
            ''')
            
            # Таблица подписок на курсы
            await db.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    course_type TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    price INTEGER,
                    payment_id TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Таблица курсов
            await db.execute('''
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    duration_days INTEGER,
                    price INTEGER,
                    description TEXT,
                    is_active INTEGER DEFAULT 1
                )
            ''')
            
            # Таблица платежей
            await db.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    payment_type TEXT,
                    status TEXT,
                    telegram_payment_id TEXT,
                    date TEXT
                )
            ''')
            
            # Добавляем курсы по умолчанию, если их нет
            cursor = await db.execute("SELECT COUNT(*) FROM courses")
            count = await cursor.fetchone()
            if count[0] == 0:
                default_courses = [
                    ("Пробный день", 1, 50, "Доступ к курсу на 1 день"),
                    ("Недельный курс", 7, 100, "Доступ к курсу на 7 дней"),
                    ("Месячный курс", 30, 220, "Доступ к курсу на 30 дней"),
                    ("Годовой курс", 365, 310, "Доступ к курсу на 365 дней")
                ]
                for course in default_courses:
                    await db.execute(
                        "INSERT INTO courses (name, duration_days, price, description) VALUES (?, ?, ?, ?)",
                        course
                    )
            
            await db.commit()
    
    async def add_user(self, user_id, username, first_name, last_name):
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, registered_date, last_activity) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, first_name, last_name, now, now)
            )
            await db.commit()
    
    async def update_activity(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET last_activity = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id)
            )
            await db.commit()
    
    async def get_user(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            return await cursor.fetchone()
    
    async def get_all_users(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM users ORDER BY registered_date DESC")
            return await cursor.fetchall()
    
    async def get_courses(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM courses WHERE is_active = 1 ORDER BY price")
            return await cursor.fetchall()
    
    async def get_course(self, course_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM courses WHERE id = ?", (course_id,))
            return await cursor.fetchone()
    
    async def update_course_price(self, course_id, new_price):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE courses SET price = ? WHERE id = ?", (new_price, course_id))
            await db.commit()
    
    async def update_course_duration(self, course_id, new_duration):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE courses SET duration_days = ? WHERE id = ?", (new_duration, course_id))
            await db.commit()
    
    async def update_course_name(self, course_id, new_name):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE courses SET name = ? WHERE id = ?", (new_name, course_id))
            await db.commit()
    
    async def add_subscription(self, user_id, course_id, payment_id):
        async with aiosqlite.connect(self.db_path) as db:
            course = await self.get_course(course_id)
            if not course:
                return False
            
            start_date = datetime.now()
            end_date = start_date + timedelta(days=course[2])  # duration_days
            
            await db.execute(
                "INSERT INTO subscriptions (user_id, course_type, start_date, end_date, price, payment_id) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, course[1], start_date.isoformat(), end_date.isoformat(), course[3], payment_id)
            )
            await db.commit()
            return True
    
    async def get_user_subscriptions(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND end_date > ? ORDER BY end_date DESC",
                (user_id, datetime.now().isoformat())
            )
            return await cursor.fetchall()
    
    async def get_all_subscriptions(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM subscriptions WHERE end_date > ? ORDER BY end_date DESC",
                (datetime.now().isoformat(),)
            )
            return await cursor.fetchall()
    
    async def add_balance(self, user_id, amount):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            await db.commit()
    
    async def get_balance(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
            return result[0] if result else 0
    
    async def set_admin(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,))
            await db.commit()
    
    async def remove_admin(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (user_id,))
            await db.commit()
    
    async def get_admins(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT user_id, username FROM users WHERE is_admin = 1")
            return await cursor.fetchall()
    
    async def get_statistics(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Общее количество пользователей
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            total_users = (await cursor.fetchone())[0]
            
            # Активные подписки
            cursor = await db.execute("SELECT COUNT(*) FROM subscriptions WHERE end_date > ?", (datetime.now().isoformat(),))
            active_subs = (await cursor.fetchone())[0]
            
            # Общая выручка
            cursor = await db.execute("SELECT SUM(price) FROM subscriptions")
            total_revenue = (await cursor.fetchone())[0] or 0
            
            # Пользователей за сегодня
            today = datetime.now().date().isoformat()
            cursor = await db.execute("SELECT COUNT(*) FROM users WHERE registered_date LIKE ?", (f"{today}%",))
            today_users = (await cursor.fetchone())[0]
            
            return {
                "total_users": total_users,
                "active_subs": active_subs,
                "total_revenue": total_revenue,
                "today_users": today_users
            }

# Инициализация базы данных
db = Database()

# Клавиатуры
def main_keyboard(is_admin=False):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="📚 Каталог курсов", callback_data="catalog"))
    builder.add(InlineKeyboardButton(text="👤 Мои курсы", callback_data="my_courses"))
    builder.add(InlineKeyboardButton(text="💰 Баланс", callback_data="balance"))
    builder.add(InlineKeyboardButton(text="📞 Поддержка", callback_data="support"))
    if is_admin:
        builder.add(InlineKeyboardButton(text="⚙️ Админ панель", callback_data="admin"))
    builder.adjust(2)
    return builder.as_markup()

def admin_keyboard():
    builder = InlineKeyboardBuilder()
    buttons = [
        ("📊 Статистика", "admin_stats"),
        ("👥 Список юзеров", "admin_users"),
        ("📋 Список подписок", "admin_subs"),
        ("💰 Пополнить баланс", "admin_add_balance"),
        ("📚 Выдать курс", "admin_give_course"),
        ("👑 Выдать админку", "admin_add_admin"),
        ("🗑 Удалить админа", "admin_remove_admin"),
        ("📝 Список админов", "admin_list_admins"),
        ("✏️ Изменить товары", "admin_edit_courses"),
        ("💰 Изменить цены", "admin_edit_prices"),
        ("⏱ Изменить время курсов", "admin_edit_duration"),
        ("📨 Рассылка", "admin_broadcast"),
        ("🏠 Главное меню", "back_to_main")
    ]
    for text, callback in buttons:
        builder.add(InlineKeyboardButton(text=text, callback_data=callback))
    builder.adjust(2)
    return builder.as_markup()

def courses_keyboard(courses):
    builder = InlineKeyboardBuilder()
    for course in courses:
        course_id, name, duration, price, desc, active = course
        builder.add(InlineKeyboardButton(
            text=f"{name} - {price} ⭐",
            callback_data=f"buy_course_{course_id}"
        ))
    builder.add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main"))
    builder.adjust(1)
    return builder.as_markup()

def edit_courses_keyboard(courses):
    builder = InlineKeyboardBuilder()
    for course in courses:
        course_id, name, duration, price, desc, active = course
        builder.add(InlineKeyboardButton(
            text=f"✏️ {name}",
            callback_data=f"edit_course_{course_id}"
        ))
    builder.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
    builder.adjust(1)
    return builder.as_markup()

# Обработчики команд
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    await db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    user_data = await db.get_user(user.id)
    is_admin = user_data[5] if user_data else False
    
    await message.answer(
        f"👋 Добро пожаловать в магазин курсов по телекинезу!\n\n"
        f"Здесь вы можете приобрести доступ к эксклюзивным курсам и научиться управлять предметами силой мысли.\n\n"
        f"Выберите действие:",
        reply_markup=main_keyboard(is_admin)
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await db.update_activity(callback.from_user.id)
    user_data = await db.get_user(callback.from_user.id)
    is_admin = user_data[5] if user_data else False
    
    await callback.message.edit_text(
        "Главное меню:",
        reply_markup=main_keyboard(is_admin)
    )

@dp.callback_query(F.data == "catalog")
async def show_catalog(callback: CallbackQuery):
    await db.update_activity(callback.from_user.id)
    courses = await db.get_courses()
    
    text = "📚 Доступные курсы:\n\n"
    for course in courses:
        course_id, name, duration, price, desc, active = course
        text += f"• {name}\n"
        text += f"  {desc}\n"
        text += f"  Длительность: {duration} дней\n"
        text += f"  Цена: {price} ⭐\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=courses_keyboard(courses)
    )

@dp.callback_query(F.data.startswith("buy_course_"))
async def buy_course(callback: CallbackQuery):
    course_id = int(callback.data.split("_")[2])
    course = await db.get_course(course_id)
    
    if not course:
        await callback.answer("Курс не найден!")
        return
    
    course_id, name, duration, price, desc, active = course
    
    # Создаем инвойс для оплаты звездами
    prices = [LabeledPrice(label=name, amount=price)]
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Покупка курса: {name}",
        description=f"{desc}\nДлительность: {duration} дней",
        payload=f"course_{course_id}",
        provider_token="",  # Для звезд не нужен
        currency="XTR",  # Звезды Telegram
        prices=prices,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Оплатить {price} ⭐", pay=True)]
        ])
    )
    
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    
    if payload.startswith("course_"):
        course_id = int(payload.split("_")[1])
        
        # Добавляем подписку
        await db.add_subscription(message.from_user.id, course_id, payment.telegram_payment_charge_id)
        
        await message.answer(
            "✅ Оплата прошла успешно!\n"
            "Курс активирован. Перейдите в раздел 'Мои курсы' для доступа к материалам.",
            reply_markup=main_keyboard(False)
        )

@dp.callback_query(F.data == "my_courses")
async def show_my_courses(callback: CallbackQuery):
    await db.update_activity(callback.from_user.id)
    subscriptions = await db.get_user_subscriptions(callback.from_user.id)
    
    if not subscriptions:
        await callback.message.edit_text(
            "У вас пока нет активных курсов.\n"
            "Приобретите курс в каталоге!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Каталог", callback_data="catalog")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")]
            ])
        )
        return
    
    text = "📋 Ваши активные курсы:\n\n"
    for sub in subscriptions:
        sub_id, user_id, course_name, start_date, end_date, price, payment_id = sub
        start = datetime.fromisoformat(start_date).strftime("%d.%m.%Y")
        end = datetime.fromisoformat(end_date).strftime("%d.%m.%Y")
        days_left = (datetime.fromisoformat(end_date) - datetime.now()).days
        
        text += f"• {course_name}\n"
        text += f"  Действует: {start} - {end}\n"
        text += f"  Осталось дней: {days_left}\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")]
        ])
    )

@dp.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    await db.update_activity(callback.from_user.id)
    balance = await db.get_balance(callback.from_user.id)
    
    await callback.message.edit_text(
        f"💰 Ваш баланс: {balance} ⭐\n\n"
        f"Звёзды можно использовать для оплаты курсов.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")]
        ])
    )

@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):
    await callback.message.edit_text(
        "📞 Поддержка\n\n"
        "По всем вопросам обращайтесь к администратору:\n"
        "@your_support_username",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")]
        ])
    )

# Админ панель
@dp.callback_query(F.data == "admin")
async def admin_panel(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    # Проверяем, является ли пользователь админом
    user_data = await db.get_user(user_id)
    if not user_data or (user_data[5] != 1 and user_id not in ADMIN_IDS):
        await callback.answer("У вас нет доступа к админ панели!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "⚙️ Панель администратора\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard()
    )

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    stats = await db.get_statistics()
    
    text = "📊 Статистика бота:\n\n"
    text += f"👥 Всего пользователей: {stats['total_users']}\n"
    text += f"📈 Активных подписок: {stats['active_subs']}\n"
    text += f"💰 Общая выручка: {stats['total_revenue']} ⭐\n"
    text += f"📅 Новых сегодня: {stats['today_users']}\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin")]
        ])
    )

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    users = await db.get_all_users()
    
    text = "👥 Список пользователей:\n\n"
    for user in users[:10]:  # Показываем только первых 10
        user_id, username, first_name, last_name, balance, is_admin, reg_date, last_act = user
        reg = datetime.fromisoformat(reg_date).strftime("%d.%m.%Y")
        name = first_name or ""
        if username:
            name += f" (@{username})"
        text += f"• {name}\n"
        text += f"  ID: {user_id}\n"
        text += f"  Баланс: {balance} ⭐\n"
        text += f"  Регистрация: {reg}\n"
        text += f"  Админ: {'Да' if is_admin else 'Нет'}\n\n"
    
    text += f"\nВсего пользователей: {len(users)}"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin")]
        ])
    )

@dp.callback_query(F.data == "admin_subs")
async def admin_subs(callback: CallbackQuery):
    subs = await db.get_all_subscriptions()
    
    text = "📋 Активные подписки:\n\n"
    for sub in subs[:10]:  # Показываем только первых 10
        sub_id, user_id, course_name, start_date, end_date, price, payment_id = sub
        start = datetime.fromisoformat(start_date).strftime("%d.%m.%Y")
        end = datetime.fromisoformat(end_date).strftime("%d.%m.%Y")
        user = await db.get_user(user_id)
        username = user[1] if user else "Unknown"
        
        text += f"• Пользователь: @{username} (ID: {user_id})\n"
        text += f"  Курс: {course_name}\n"
        text += f"  Действует: {start} - {end}\n"
        text += f"  Цена: {price} ⭐\n\n"
    
    text += f"\nВсего активных подписок: {len(subs)}"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin")]
        ])
    )

@dp.callback_query(F.data == "admin_add_balance")
async def admin_add_balance_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💰 Пополнение баланса\n\n"
        "Введите ID пользователя и сумму через пробел\n"
        "Пример: `123456789 100`",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_balance_amount)

@dp.message(AdminStates.waiting_for_balance_amount)
async def admin_add_balance_process(message: types.Message, state: FSMContext):
    try:
        user_id, amount = map(int, message.text.split())
        await db.add_balance(user_id, amount)
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"💰 Ваш баланс пополнен на {amount} ⭐ администратором!"
            )
        except:
            pass
        
        await message.answer(
            f"✅ Баланс пользователя {user_id} пополнен на {amount} ⭐",
            reply_markup=admin_keyboard()
        )
    except:
        await message.answer(
            "❌ Неверный формат. Используйте: ID СУММА\nПример: 123456789 100"
        )
    
    await state.clear()

@dp.callback_query(F.data == "admin_give_course")
async def admin_give_course_start(callback: CallbackQuery, state: FSMContext):
    courses = await db.get_courses()
    text = "📚 Выберите курс для выдачи:\n\n"
    
    keyboard = InlineKeyboardBuilder()
    for course in courses:
        course_id, name, duration, price, desc, active = course
        keyboard.add(InlineKeyboardButton(
            text=name,
            callback_data=f"give_course_select_{course_id}"
        ))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
    keyboard.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("give_course_select_"))
async def admin_give_course_select(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split("_")[3])
    await state.update_data(give_course_id=course_id)
    
    await callback.message.edit_text(
        "Введите ID пользователя, которому хотите выдать курс:"
    )
    await state.set_state(AdminStates.waiting_for_course_give)

@dp.message(AdminStates.waiting_for_course_give)
async def admin_give_course_process(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        data = await state.get_data()
        course_id = data['give_course_id']
        
        # Добавляем подписку
        await db.add_subscription(user_id, course_id, f"admin_gift_{datetime.now().timestamp()}")
        
        # Уведомляем пользователя
        course = await db.get_course(course_id)
        try:
            await bot.send_message(
                user_id,
                f"🎁 Вам выдан курс '{course[1]}' администратором!"
            )
        except:
            pass
        
        await message.answer(
            f"✅ Курс успешно выдан пользователю {user_id}",
            reply_markup=admin_keyboard()
        )
    except:
        await message.answer("❌ Неверный ID пользователя")
    
    await state.clear()

@dp.callback_query(F.data == "admin_add_admin")
async def admin_add_admin_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите ID пользователя, которому хотите выдать права администратора:"
    )
    await state.set_state(AdminStates.waiting_for_admin_add)

@dp.message(AdminStates.waiting_for_admin_add)
async def admin_add_admin_process(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        await db.set_admin(user_id)
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                "👑 Вам выданы права администратора!"
            )
        except:
            pass
        
        await message.answer(
            f"✅ Пользователь {user_id} теперь администратор",
            reply_markup=admin_keyboard()
        )
    except:
        await message.answer("❌ Неверный ID пользователя")
    
    await state.clear()

@dp.callback_query(F.data == "admin_remove_admin")
async def admin_remove_admin_start(callback: CallbackQuery, state: FSMContext):
    admins = await db.get_admins()
    
    if len(admins) <= 1 and callback.from_user.id in ADMIN_IDS:
        await callback.answer("Нельзя удалить последнего администратора!", show_alert=True)
        return
    
    text = "Список администраторов:\n\n"
    keyboard = InlineKeyboardBuilder()
    
    for admin_id, username in admins:
        if admin_id != callback.from_user.id:  # Не даем удалить самого себя
            text += f"• ID: {admin_id} (@{username})\n"
            keyboard.add(InlineKeyboardButton(
                text=f"Удалить @{username}",
                callback_data=f"remove_admin_{admin_id}"
            ))
    
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
    keyboard.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("remove_admin_"))
async def admin_remove_admin_process(callback: CallbackQuery):
    admin_id = int(callback.data.split("_")[2])
    
    if admin_id in ADMIN_IDS:
        await callback.answer("Нельзя удалить создателя бота!", show_alert=True)
        return
    
    await db.remove_admin(admin_id)
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            admin_id,
            "👤 Ваши права администратора были отозваны."
        )
    except:
        pass
    
    await callback.message.edit_text(
        f"✅ Администратор {admin_id} удален",
        reply_markup=admin_keyboard()
    )

@dp.callback_query(F.data == "admin_list_admins")
async def admin_list_admins(callback: CallbackQuery):
    admins = await db.get_admins()
    
    text = "👑 Список администраторов:\n\n"
    for admin_id, username in admins:
        role = "Создатель" if admin_id in ADMIN_IDS else "Администратор"
        text += f"• ID: {admin_id}\n"
        text += f"  Username: @{username}\n"
        text += f"  Роль: {role}\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin")]
        ])
    )

@dp.callback_query(F.data == "admin_edit_courses")
async def admin_edit_courses(callback: CallbackQuery):
    courses = await db.get_courses()
    
    await callback.message.edit_text(
        "✏️ Редактирование курсов\n\n"
        "Выберите курс для редактирования:",
        reply_markup=edit_courses_keyboard(courses)
    )

@dp.callback_query(F.data.startswith("edit_course_"))
async def admin_edit_course(callback: CallbackQuery, state: FSMContext):
    course_id = int(callback.data.split("_")[2])
    course = await db.get_course(course_id)
    
    await state.update_data(edit_course_id=course_id)
    
    text = f"Редактирование курса: {course[1]}\n\n"
    text += f"Текущее название: {course[1]}\n"
    text += f"Текущая цена: {course[3]} ⭐\n"
    text += f"Текущая длительность: {course[2]} дней\n\n"
    text += "Что хотите изменить?"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✏️ Название", callback_data="edit_name"))
    keyboard.add(InlineKeyboardButton(text="💰 Цену", callback_data="edit_price"))
    keyboard.add(InlineKeyboardButton(text="⏱ Длительность", callback_data="edit_duration"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_edit_courses"))
    keyboard.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "edit_name")
async def admin_edit_course_name(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите новое название курса:"
    )
    await state.set_state(AdminStates.waiting_for_course_change)

@dp.callback_query(F.data == "edit_price")
async def admin_edit_course_price(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите новую цену курса (в звездах):"
    )
    await state.set_state(AdminStates.waiting_for_price_change)

@dp.callback_query(F.data == "edit_duration")
async def admin_edit_course_duration(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите новую длительность курса (в днях):"
    )
    await state.set_state(AdminStates.waiting_for_duration_change)

@dp.message(AdminStates.waiting_for_course_change)
async def admin_update_course_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    course_id = data['edit_course_id']
    
    await db.update_course_name(course_id, message.text)
    
    await message.answer(
        "✅ Название курса обновлено",
        reply_markup=admin_keyboard()
    )
    await state.clear()

@dp.message(AdminStates.waiting_for_price_change)
async def admin_update_course_price(message: types.Message, state: FSMContext):
    try:
        price = int(message.text)
        data = await state.get_data()
        course_id = data['edit_course_id']
        
        await db.update_course_price(course_id, price)
        
        await message.answer(
            "✅ Цена курса обновлена",
            reply_markup=admin_keyboard()
        )
    except:
        await message.answer("❌ Введите корректное число")
    
    await state.clear()

@dp.message(AdminStates.waiting_for_duration_change)
async def admin_update_course_duration(message: types.Message, state: FSMContext):
    try:
        duration = int(message.text)
        data = await state.get_data()
        course_id = data['edit_course_id']
        
        await db.update_course_duration(course_id, duration)
        
        await message.answer(
            "✅ Длительность курса обновлена",
            reply_markup=admin_keyboard()
        )
    except:
        await message.answer("❌ Введите корректное число")
    
    await state.clear()

@dp.callback_query(F.data == "admin_edit_prices")
async def admin_edit_prices(callback: CallbackQuery):
    courses = await db.get_courses()
    
    text = "💰 Текущие цены курсов:\n\n"
    for course in courses:
        course_id, name, duration, price, desc, active = course
        text += f"• {name}: {price} ⭐\n"
    
    text += "\nДля изменения цены используйте раздел 'Изменить товары'"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить товары", callback_data="admin_edit_courses")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin")]
        ])
    )

@dp.callback_query(F.data == "admin_edit_duration")
async def admin_edit_duration(callback: CallbackQuery):
    courses = await db.get_courses()
    
    text = "⏱ Текущая длительность курсов:\n\n"
    for course in courses:
        course_id, name, duration, price, desc, active = course
        text += f"• {name}: {duration} дней\n"
    
    text += "\nДля изменения длительности используйте раздел 'Изменить товары'"
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить товары", callback_data="admin_edit_courses")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin")]
        ])
    )

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📨 Рассылка\n\n"
        "Отправьте сообщение для рассылки всем пользователям:"
    )
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast)
async def admin_broadcast_process(message: types.Message, state: FSMContext):
    users = await db.get_all_users()
    sent = 0
    failed = 0
    
    await message.answer("⏳ Начинаю рассылку...")
    
    for user in users:
        user_id = user[0]
        try:
            await bot.send_message(
                user_id,
                f"📢 Рассылка от администратора:\n\n{message.text}"
            )
            sent += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)  # Чтобы не спамить
    
    await message.answer(
        f"✅ Рассылка завершена\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Не доставлено: {failed}",
        reply_markup=admin_keyboard()
    )
    
    await state.clear()

# Запуск бота
async def main():
    # Инициализируем БД
    await db.init_db()
    
    # Добавляем создателя как админа, если его нет
    for admin_id in ADMIN_IDS:
        await db.set_admin(admin_id)
    
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
