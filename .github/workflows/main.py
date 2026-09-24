 import os
import time
import threading
from flask import Flask
import telebot
from telebot import types

# --- 1. ВЕБ-СЕРВЕР ДЛЯ МОНИТОРИНГА (UPTIMEROBOT) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "RandomGodBot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. ИНИЦИАЛИЗА БОТА ---
TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# Хранилище данных розыгрышей (в памяти)
# Для профессионального продакшена обычно используют базы данных (SQLite/PostgreSQL)
giveaways = {}
active_giveaway_id = None

# --- 3. ПАНЕЛЬ В ЛИЧНЫХ СООБЩЕНИЯХ ---
@bot.message_handler(commands=['start', 'admin'])
def start_private(message):
    if message.chat.type != 'private':
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_create = types.InlineKeyboardButton("🎁 Создать розыгрыш", callback_data="create_giveaway")
    btn_status = types.InlineKeyboardButton("📊 Статус текущего розыгрыша", callback_data="status_giveaway")
    btn_finish = types.InlineKeyboardButton("🛑 Завершить розыгрыш вручную", callback_data="finish_manual")
    markup.add(btn_create, btn_status, btn_finish)

    bot.send_message(
        message.chat.id,
        "👑 **Панель управления RandomGodBot**\n\n"
        "Здесь вы можете настроить и запустить розыгрыш для вашего канала.",
        parse_mode="Markdown",
        reply_markup=markup
    )

# --- 4. ОБРАБОТКА ИНТЕРАКТИВНЫХ КНОПОК ПАНЕЛИ ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    global active_giveaway_id, giveaways

    # --- Создание розыгрыша ---
    if call.data == "create_giveaway":
        msg = bot.send_message(
            call.message.chat.id,
            "📝 **Шаг 1 из 4:** Отправьте ID или @username канала (например, `@my_channel`).\n"
            "⚠️ *Бот должен быть добавлен в этот канал как администратор!*"
        )
        bot.register_next_step_handler(msg, process_channel)

    # --- Статус текущего розыгрыша ---
    elif call.data == "status_giveaway":
        if not active_giveaway_id or active_giveaway_id not in giveaways:
            bot.answer_callback_query(call.id, "❌ Нет активных розыгрышей!", show_alert=True)
            return

        g = giveaways[active_giveaway_id]
        bot.send_message(
            call.message.chat.id,
            f"📊 **Информация о розыгрыше:**\n\n"
            f"🎁 Приз: {g['prize']}\n"
            f"📢 Канал: {g['channel']}\n"
            f"👥 Участников: {len(g['participants'])}\n"
            f"🎯 Условие завершения: {g['condition_text']}"
        )
        bot.answer_callback_query(call.id)

    # --- Ручное завершение ---
    elif call.data == "finish_manual":
        if not active_giveaway_id or active_giveaway_id not in giveaways:
            bot.answer_callback_query(call.id, "❌ Нет активных розыгрышей!", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, "Завершаем розыгрыш...")
        finish_giveaway(active_giveaway_id, reason="Завершен администратором")

    # --- Участие в розыгрыше из канала ---
    elif call.data.startswith("join_"):
        g_id = call.data.split("join_")[1]
        if g_id not in giveaways or not giveaways[g_id]['active']:
            bot.answer_callback_query(call.id, "❌ Этот розыгрыш уже завершен!", show_alert=True)
            return

        g = giveaways[g_id]
        user_id = call.from_user.id

        # Проверка подписки на канал
        try:
            member = bot.get_chat_member(g['channel'], user_id)
            if member.status in ['left', 'kicked']:
                bot.answer_callback_query(call.id, "⚠️ Вы не подписаны на канал! Подпишитесь и попробуйте снова.", show_alert=True)
                return
        except Exception as e:
            bot.answer_callback_query(call.id, "❌ Ошибка проверки подписки. Убедитесь, что бот — админ канала.", show_alert=True)
            return

        # Добавление в участники
        if user_id in g['participants']:
            bot.answer_callback_query(call.id, "👌 Вы уже участвуете в розыгрыше!", show_alert=True)
        else:
            g['participants'].append(user_id)
            bot.answer_callback_query(call.id, "🎉 Вы успешно зарегистрированы в розыгрыше!", show_alert=True)
            update_giveaway_post(g_id)

            # Проверка завершения по количеству участников
            if g['mode'] == 'limit' and len(g['participants']) >= g['limit']:
                finish_giveaway(g_id, reason="Достигнут лимит участников")

# --- 5. Пошаговое создание розыгрыша ---
def process_channel(message):
    channel = message.text.strip()
    msg = bot.send_message(message.chat.id, "🎁 **Шаг 2 из 4:** Введите описание приза (например: `Telegram Premium на 1 год`):")
    bot.register_next_step_handler(msg, process_prize, channel)

def process_prize(message, channel):
    prize = message.text.strip()
    
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("По кол-ву участников", "По таймеру (в минутах)", "Ручное окончание")
    
    msg = bot.send_message(
        message.chat.id,
        "⏳ **Шаг 3 из 4:** Выберите тип завершения розыгрыша:",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_mode, channel, prize)

def process_mode(message, channel, prize):
    mode_text = message.text.strip()
    
    if mode_text == "По кол-ву участников":
        msg = bot.send_message(message.chat.id, "🔢 Введите необходимое количество участников (например, `50`):", reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(msg, finalize_giveaway, channel, prize, 'limit')
    elif mode_text == "По таймеру (в минутах)":
        msg = bot.send_message(message.chat.id, "⏱️ Введите время проведения в минутах (например, `60`):", reply_markup=types.ReplyKeyboardRemove())
        bot.register_next_step_handler(msg, finalize_giveaway, channel, prize, 'timer')
    else:
        finalize_giveaway(message, channel, prize, 'manual', val=0)

def finalize_giveaway(message, channel, prize, mode, val=None):
    global active_giveaway_id
    
    if val is None:
        try:
            val = int(message.text.strip())
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: нужно ввести число. Начните создание заново в меню.")
            return

    g_id = str(int(time.time()))
    active_giveaway_id = g_id

    cond_text = "Ручное завершение"
    if mode == 'limit':
        cond_text = f"До {val} участников"
    elif mode == 'timer':
        cond_text = f"Завершение через {val} мин."

    giveaways[g_id] = {
        'id': g_id,
        'channel': channel,
        'prize': prize,
        'mode': mode,
        'limit': val if mode == 'limit' else 0,
        'timer': val if mode == 'timer' else 0,
        'condition_text': cond_text,
        'participants': [],
        'active': True,
        'message_id': None
    }

    # Публикация поста в канал
    markup = types.InlineKeyboardMarkup()
    btn_join = types.InlineKeyboardButton("Участвовать 🎉 (0)", callback_data=f"join_{g_id}")
    markup.add(btn_join)

    post_text = (
        f"🎉 **РОЗЫГРЫШ!**\n\n"
        f"🏆 **Приз:** {prize}\n"
        f"📌 **Условие:** Подписка на этот канал\n"
        f"🎯 **Завершение:** {cond_text}\n\n"
        f"Нажмите кнопку ниже, чтобы принять участие!"
    )

    try:
        sent_msg = bot.send_message(channel, post_text, parse_mode="Markdown", reply_markup=markup)
        giveaways[g_id]['message_id'] = sent_msg.message_id

        bot.send_message(message.chat.id, f"✅ Розыгрыш успешно запущен в канале {channel}!")

        # Если установлен таймер — запускаем поток
        if mode == 'timer':
            threading.Thread(target=timer_worker, args=(g_id, val * 60)).start()

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Не удалось отправить пост в канал: {e}\nПроверьте права бота в канале.")

# --- 6. ОБНОВЛЕНИЕ СЧЕТЧИКА В ПОСТЕ ---
def update_giveaway_post(g_id):
    g = giveaways.get(g_id)
    if not g or not g['active']:
        return

    markup = types.InlineKeyboardMarkup()
    btn_join = types.InlineKeyboardButton(f"Участвовать 🎉 ({len(g['participants'])})", callback_data=f"join_{g_id}")
    markup.add(btn_join)

    post_text = (
        f"🎉 **РОЗЫГРЫШ!**\n\n"
        f"🏆 **Приз:** {g['prize']}\n"
        f"📌 **Условие:** Подписка на этот канал\n"
        f"🎯 **Завершение:** {g['condition_text']}\n\n"
        f"Нажмите кнопку ниже, чтобы принять участие!"
    )

    try:
        bot.edit_message_text(
            chat_id=g['channel'],
            message_id=g['message_id'],
            text=post_text,
            parse_mode="Markdown",
            reply_markup=markup
        )
    except Exception:
        pass

# --- 7. ТАЙМЕР И ЗАВЕРШЕНИЕ РОЗЫГРЫША ---
def timer_worker(g_id, seconds):
    time.sleep(seconds)
    if g_id in giveaways and giveaways[g_id]['active']:
        finish_giveaway(g_id, reason="Время вышло")

def finish_giveaway(g_id, reason=""):
    import random
    g = giveaways.get(g_id)
    if not g or not g['active']:
        return

    g['active'] = False

    if not g['participants']:
        win_text = f"🛑 **Розыгрыш завершен** ({reason}).\n\nК сожалению, никто не принял участие."
    else:
        winner_id = random.choice(g['participants'])
        try:
            winner_user = bot.get_chat_member(g['channel'], winner_id).user
            winner_mention = f"[{winner_user.first_name}](tg://user?id={winner_id})"
        except Exception:
            winner_mention = f"ID: {winner_id}"

        win_text = (
            f"🎊 **РОЗЫГРЫШ ЗАВЕРШЕН!**\n\n"
            f"🏆 **Приз:** {g['prize']}\n"
            f"👑 **Победитель:** {winner_mention}\n\n"
            f"Поздравляем победителя!"
        )

    try:
        bot.edit_message_text(
            chat_id=g['channel'],
            message_id=g['message_id'],
            text=win_text,
            parse_mode="Markdown",
            reply_markup=None
        )
    except Exception as e:
        print(f"Ошибка при обновлении итогов: {e}")

# --- 8. ЗАПУСК БОТА ---
if __name__ == '__main__':
    server_thread = threading.Thread(target=run_web)
    server_thread.daemon = True
    server_thread.start()

    print("RandomGodBot успешно запущен...")
    bot.infinity_polling()
