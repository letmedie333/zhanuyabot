import os
import time
import uuid
import logging
import requests
import psycopg2
from PIL import Image
from datetime import datetime, timedelta, timezone
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
from whatsapp_api_client_python import API
from dotenv import load_dotenv
from waitress import serve

# ==========================================
# 1. НАСТРОЙКИ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[logging.FileHandler("bot.log", encoding='utf-8'), logging.StreamHandler()]
)

# Создаем зону Казахстана (UTC+5)
KZ_TZ = timezone(timedelta(hours=5))
load_dotenv()
ID_INSTANCE = os.getenv("GREEN_API_ID")
API_TOKEN_INSTANCE = os.getenv("GREEN_API_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "77000000000")
DATABASE_URL = os.getenv("DATABASE_URL")

if not all([ID_INSTANCE, API_TOKEN_INSTANCE, WEBHOOK_SECRET, DATABASE_URL]):
    logging.critical("❌ Ошибка: Отсутствуют критические переменные окружения в .env (включая DATABASE_URL)!")
    raise ValueError("Критическая ошибка конфигурации.")

# Лимит обращений в день установлен на 5
MAX_DAILY_LIMIT = 5
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TEXT_LENGTH = 3000

# Расширенный список разрешенных форматов
ALLOWED_MIME_TYPES = [
    'image/jpeg', 'image/png', 'audio/ogg', 'audio/aac', 
    'audio/mp4', 'audio/amr', 'audio/mpeg', 'application/ogg', 'video/mp4'
]

greenAPI = API.GreenApi(ID_INSTANCE, API_TOKEN_INSTANCE)
app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=5)

# ==========================================
# 2. ИНИЦИАЛИЗАЦИЯ БД И АРХИТЕКТУРА
# ==========================================
MEDIA_FOLDER = "media_files"
os.makedirs(MEDIA_FOLDER, exist_ok=True)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with closing(get_db_connection()) as conn:
        with conn:  # Авто-коммит
            with conn.cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS complaints (
                        id SERIAL PRIMARY KEY,
                        phone TEXT,
                        language TEXT,
                        address TEXT,
                        category TEXT,
                        text_message TEXT,
                        media_path TEXT,
                        status TEXT DEFAULT 'new',
                        priority TEXT DEFAULT 'medium',
                        tags TEXT DEFAULT '',
                        assigned_to TEXT,
                        created_at TIMESTAMP,
                        response_at TIMESTAMP,
                        updated_at TIMESTAMP,
                        closed_at TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS complaint_history (
                        id SERIAL PRIMARY KEY,
                        complaint_id INTEGER,
                        changed_by TEXT,
                        action TEXT,
                        old_value TEXT,
                        new_value TEXT,
                        changed_at TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        phone TEXT PRIMARY KEY,
                        step TEXT,
                        lang TEXT,
                        address TEXT,
                        category TEXT,
                        updated_at TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS processed_messages (
                        message_id TEXT PRIMARY KEY,
                        processed_at TIMESTAMP
                    )
                ''')
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_phone ON complaints(phone);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON complaints(status);")

try:
    init_db()
except Exception as e:
    logging.error(f"Ошибка инициализации таблиц бота: {e}")

# ==========================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И ДАННЫЕ
# ==========================================
def notify_admin(error_text, req_id):
    try:
        greenAPI.sending.sendMessage(f"{ADMIN_PHONE}@c.us", f"🚨 CRITICAL ALERT [{req_id}]\n{error_text}")
    except Exception as e:
        logging.error(f"[{req_id}] Не удалось отправить алерт админу: {e}")

def safe_send(chat_id, text, req_id):
    try:
        response = greenAPI.sending.sendMessage(chat_id, text)
        code = getattr(response, 'code', None)
        if code and code not in (200, 201):
            logging.error(f"[{req_id}] API Error {code} для {chat_id}")
    except Exception as e:
        logging.error(f"[{req_id}] Ошибка сети при отправке: {e}")

def is_new_message(message_id):
    with closing(get_db_connection()) as conn:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO processed_messages (message_id, processed_at) VALUES (%s, %s) ON CONFLICT (message_id) DO NOTHING",
                    (message_id, datetime.now(KZ_TZ).strftime('%Y-%m-%d %H:%M:%S'))
                )
                return cursor.rowcount > 0

def is_duplicate_complaint(phone, text):
    if not text: return False
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            time_limit = (datetime.now(KZ_TZ) - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute(
                "SELECT text_message FROM complaints WHERE phone = %s AND created_at >= %s ORDER BY created_at DESC LIMIT 1",
                (phone, time_limit)
            )
            res = cursor.fetchone()
            return res and res[0] == text

def get_session(phone):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT step, lang, address, category, updated_at FROM user_sessions WHERE phone = %s", (phone,))
            res = cursor.fetchone()
            if res:
                updated_at = res[4]
                # В PostgreSQL это уже объект datetime, но на всякий случай проверяем
                if isinstance(updated_at, str):
                    updated_at = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S')
                
                updated_at = updated_at.replace(tzinfo=KZ_TZ) if updated_at.tzinfo is None else updated_at
                
                if datetime.now(KZ_TZ) - updated_at > timedelta(hours=24):
                    with conn:
                        cursor.execute("DELETE FROM user_sessions WHERE phone = %s", (phone,))
                    return None
                return {'step': res[0], 'lang': res[1], 'address': res[2], 'category': res[3]}
        return None

def update_session(phone, step, lang=None, address=None, category=None):
    now = datetime.now(KZ_TZ).strftime('%Y-%m-%d %H:%M:%S')
    with closing(get_db_connection()) as conn:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO user_sessions (phone, step, lang, address, category, updated_at) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (phone) DO UPDATE SET 
                        step=EXCLUDED.step, 
                        lang=COALESCE(EXCLUDED.lang, user_sessions.lang), 
                        address=COALESCE(EXCLUDED.address, user_sessions.address),
                        category=COALESCE(EXCLUDED.category, user_sessions.category),
                        updated_at=EXCLUDED.updated_at
                """, (phone, step, lang, address, category, now))

def clear_session(phone):
    with closing(get_db_connection()) as conn:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM user_sessions WHERE phone = %s", (phone,))

def get_daily_complaint_count(phone_number):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            today_str = datetime.now(KZ_TZ).strftime('%Y-%m-%d')
            cursor.execute(
                "SELECT COUNT(*) FROM complaints WHERE phone = %s AND DATE(created_at) = %s",
                (phone_number, today_str)
            )
            res = cursor.fetchone()
            return res[0] if res else 0

# Обновленные списки
ADDRESSES = {
    '1': 'г.Жезказган, Алашахана 34', '2': 'г.Жезказган, Анаркулова 9',
    '3': 'г.Жезказган, Ауэзова 56', '4': 'г.Жезказган, Женис 11',
    '5': 'г.Сатпаев, Абая Кунанбаева 113', '6': 'г.Сатпаев, Абая Кунанбаева 34',
    '7': 'г.Сатпаев, Ердена 161В', '8': 'г.Сатпаев, Ердена 215-1',
    '9': 'г.Сатпаев, Ердена 41-1', '10': 'г.Сатпаев, Мангелек Ел 76',
    '11': 'г.Сатпаев, Наурыз 8а', '12': 'г.Сатпаев, Независимости 17/1',
    '13': 'г.Сатпаев, Сатпаева 139А', '14': 'г.Сатпаев, Сатпаева 143',
    '15': 'с.Кенгир, Ауезова 27 (ГИПЕРМАРКЕТ)', '16': 'Басқа / Другой'
}
CATEGORIES = {'1': 'complaint', '2': 'suggestion'}

# ==========================================
# 4. ЛОГИКА ОБРАБОТКИ
# ==========================================
def process_message(body, req_id):
    try:
        if body.get('typeWebhook') != 'incomingMessageReceived': return
        
        message_id = body.get('idMessage')
        if not message_id or not is_new_message(message_id):
            logging.info(f"[{req_id}] Сообщение {message_id} уже обработано. Игнорируем.")
            return

        msg_data = body.get('messageData', {})
        chat_id = body.get('senderData', {}).get('sender', '')
        if not chat_id or '@g.us' in chat_id: return

        phone_number = chat_id.replace('@c.us', '')
        msg_type = msg_data.get('typeMessage', '')
        
        # Чтение текста
        text = ""
        if msg_type == 'textMessage':
            text = msg_data.get('textMessageData', {}).get('textMessage', '')
        elif msg_type == 'extendedTextMessage':
            text = msg_data.get('extendedTextMessageData', {}).get('text', '')
        text = text.strip()
        
        is_text_msg = msg_type in ['textMessage', 'extendedTextMessage']

        masked_phone = f"{phone_number[:4]}****{phone_number[-3:]}" if len(phone_number) > 7 else "****"
        logging.info(f"[{req_id}] Сообщение от: {masked_phone} | Тип: {msg_type} | Текст: {text}")

        WELCOME_MSG = (
            "Сәлеметсіз бе! «Жанұя» маркеттер желісінің қолдау қызметіне қош келдіңіз! 👋\n"
            "Сіздің пікіріңіз біз үшін өте маңызды. Жалғастыру үшін тілді таңдаңыз:\n"
            "1 - Қазақ тілі\n\n"
            "Здравствуйте! Добро пожаловать в службу поддержки сети маркетов «Жанұя»! 👋\n"
            "Ваше мнение очень важно для нас. Выберите язык:\n"
            "2 - Русский язык"
        )

        session = get_session(phone_number)

        if not session:
            if get_daily_complaint_count(phone_number) >= MAX_DAILY_LIMIT:
                safe_send(chat_id, f"Извините, вы исчерпали дневной лимит ({MAX_DAILY_LIMIT} обращений).", req_id)
                return
            update_session(phone_number, step='CHOOSE_LANGUAGE')
            safe_send(chat_id, WELCOME_MSG, req_id)
            return

        step, lang = session['step'], session['lang']

        if step == 'CHOOSE_LANGUAGE':
            if is_text_msg and text in ['1', '2']:
                new_lang = 'kz' if text == '1' else 'ru'
                update_session(phone_number, step='CHOOSE_ADDRESS', lang=new_lang)
                address_list = "\n".join([f"📍 {k}. {v}" for k, v in ADDRESSES.items()])
                msg = f"Қай маркет бойынша хабарласып отырсыз? Нөмірді таңдаңыз:\n\n{address_list}" if new_lang == 'kz' else f"По какому маркету вы обращаетесь? Выберите номер:\n\n{address_list}"
                safe_send(chat_id, msg, req_id)
            else:
                safe_send(chat_id, "1 - Қазақша / 2 - Русский", req_id)

        elif step == 'CHOOSE_ADDRESS':
            if is_text_msg and text in ADDRESSES:
                update_session(phone_number, step='CHOOSE_CATEGORY', address=ADDRESSES[text])
                msg = "Қабылдаймыз:\n1 - Шағым (Жалоба)\n2 - Ұсыныс (Предложение)" if lang == 'kz' else "Выберите категорию:\n1 - Жалоба\n2 - Предложение"
                safe_send(chat_id, msg, req_id)
            else:
                safe_send(chat_id, "Мекенжайды тізімнен таңдаңыз (1-16)." if lang == 'kz' else "Выберите адрес из списка (1-16).", req_id)

        elif step == 'CHOOSE_CATEGORY':
            if is_text_msg and text in CATEGORIES:
                update_session(phone_number, step='WAITING_FOR_FEEDBACK', category=CATEGORIES[text])
                msg = "Жақсы. Енді хабарламаңызды жазыңыз (мәтін немесе фото жіберуге болады)." if lang == 'kz' else "Отлично. Напишите ваше сообщение (можно прикрепить фото/аудио)."
                safe_send(chat_id, msg, req_id)
            else:
                safe_send(chat_id, "Өтінемін, 1 немесе 2 нөмірін таңдаңыз." if lang == 'kz' else "Пожалуйста, выберите 1 или 2.", req_id)

        elif step == 'WAITING_FOR_FEEDBACK':
            if get_daily_complaint_count(phone_number) >= MAX_DAILY_LIMIT:
                safe_send(chat_id, "Лимит исчерпан.", req_id)
                clear_session(phone_number)
                return

            address, category = session['address'], session['category']
            complaint_text, local_media_path = text, ""
            priority = 'high' if category == 'complaint' else 'low'

            if is_text_msg and len(text) > MAX_TEXT_LENGTH:
                safe_send(chat_id, f"Текст слишком длинный (максимум {MAX_TEXT_LENGTH} символов).", req_id)
                return

            if is_text_msg and len(text) > 10 and is_duplicate_complaint(phone_number, text):
                safe_send(chat_id, "Вы уже отправляли это сообщение за последние сутки.", req_id)
                clear_session(phone_number)
                return

            if 'fileMessageData' in msg_data or msg_type in ['imageMessage', 'audioMessage', 'videoMessage']:
                file_data = msg_data.get('fileMessageData', msg_data.get(f"{msg_type}Data", {}))
                complaint_text = file_data.get('caption', '')
                download_url = file_data.get('downloadUrl')
                
                if download_url:
                    try:
                        res = requests.get(download_url, stream=True, timeout=15)
                        if res.status_code != 200:
                            safe_send(chat_id, "Ошибка загрузки файла.", req_id)
                            return
                            
                        # Очищаем content_type от кодеков и прочего мусора
                        raw_content_type = res.headers.get('Content-Type', '')
                        content_type = raw_content_type.split(';')[0].strip().lower()
                        
                        if content_type not in ALLOWED_MIME_TYPES:
                            logging.warning(f"[{req_id}] Заблокирован файл с типом: {raw_content_type}")
                            safe_send(chat_id, "Неподдерживаемый формат файла.", req_id)
                            return
                            
                        if int(res.headers.get('Content-Length', 0)) > MAX_FILE_SIZE:
                            safe_send(chat_id, "Файл слишком большой.", req_id)
                            return

                        ext = content_type.split('/')[-1]
                        filename = f"{phone_number}_{int(time.time())}.{ext}"
                        local_media_path = os.path.join(MEDIA_FOLDER, filename)
                        
                        downloaded_size = 0
                        with open(local_media_path, 'wb') as f:
                            for chunk in res.iter_content(chunk_size=8192):
                                downloaded_size += len(chunk)
                                if downloaded_size > MAX_FILE_SIZE:
                                    f.close()
                                    os.remove(local_media_path)
                                    safe_send(chat_id, "Файл слишком большой.", req_id)
                                    return
                                f.write(chunk)

                        if content_type.startswith('image/'):
                            try:
                                with Image.open(local_media_path) as img:
                                    img.verify()
                            except Exception:
                                os.remove(local_media_path)
                                safe_send(chat_id, "Файл поврежден.", req_id)
                                return

                    except requests.exceptions.RequestException as e:
                        logging.error(f"[{req_id}] Ошибка скачивания: {e}")
                        safe_send(chat_id, "Ошибка сети.", req_id)
                        return

            elif not is_text_msg:
                safe_send(chat_id, "Отправьте текст или фото/аудио.", req_id)
                return

            with closing(get_db_connection()) as conn:
                with conn:
                    with conn.cursor() as cursor:
                        now = datetime.now(KZ_TZ).strftime('%Y-%m-%d %H:%M:%S')
                        # Запись жалобы и получение её ID
                        cursor.execute(
                            "INSERT INTO complaints (phone, language, address, category, text_message, media_path, status, priority, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, 'new', %s, %s, %s) RETURNING id",
                            (phone_number, lang, address, category, complaint_text, local_media_path, priority, now, now)
                        )
                        complaint_id = cursor.fetchone()[0]
                        
                        # Запись в историю
                        cursor.execute(
                            "INSERT INTO complaint_history (complaint_id, changed_by, action, old_value, new_value, changed_at) VALUES (%s, %s, %s, %s, %s, %s)",
                            (complaint_id, 'system', 'created', '', 'new', now)
                        )
                    
            clear_session(phone_number)
            
            current_count = get_daily_complaint_count(phone_number)
            
            limit_msg_kz = f"\n\n📊 Бүгінгі өтініштер: {current_count}/{MAX_DAILY_LIMIT}\n🏢 «Жанұя» маркеттер желісі"
            limit_msg_ru = f"\n\n📊 Обращений сегодня: {current_count}/{MAX_DAILY_LIMIT}\n🏢 Сеть маркетов «Жанұя»"
            
            reply = (
                f"✅ Рахмет! Сіздің өтінішіңіз қабылданды. Біз оны міндетті түрде қарастырамыз!{limit_msg_kz}\n\n"
                "🔄 Келесі өтініш қалдыру үшін кез келген хабарлама жіберіңіз."
            ) if lang == 'kz' else (
                f"✅ Спасибо! Ваше обращение принято. Мы обязательно его рассмотрим!{limit_msg_ru}\n\n"
                "🔄 Для нового обращения отправьте любое сообщение."
            )
            
            safe_send(chat_id, reply, req_id)

    except Exception as e:
        logging.error(f"[{req_id}] Критическая ошибка: {e}", exc_info=True)
        notify_admin(f"Ошибка в боте: {e}", req_id)

# ==========================================
# 5. WEBHOOK МАРШРУТ
# ==========================================
@app.route(f'/webhook/<secret>', methods=['POST'])
def receive_webhook(secret):
    if secret != WEBHOOK_SECRET:
        return jsonify({"error": "Forbidden"}), 403

    if request.json:
        req_id = str(uuid.uuid4())[:8]
        executor.submit(process_message, request.json, req_id)
        
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    logging.info("🚀 Бот запущен (PostgreSQL Mode)")
    port = int(os.environ.get("PORT", 8000))
    serve(app, host='0.0.0.0', port=port, threads=5, connection_limit=500)