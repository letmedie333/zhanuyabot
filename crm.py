import pandas as pd
from flask import Flask, render_template_string, request, redirect, url_for, send_file, session, g
from io import BytesIO
from flask import send_from_directory
import os
from dotenv import load_dotenv
from waitress import serve
import traceback
import sqlite3
from datetime import datetime, timedelta

app = Flask(__name__)
load_dotenv()

# Настройки безопасности и сессий
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback-secret-key-change-it")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24) # Сессия живет 24 часа
)

ADMIN_USERNAME = os.getenv("CRM_USER")
ADMIN_PASSWORD = os.getenv("CRM_PASS")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "complaints.db")

# ==========================================
# ОПТИМИЗИРОВАННАЯ РАБОТА С БД
# ==========================================
def get_db():
    """Возвращает соединение с БД для текущего контекста запроса."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH, timeout=15)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Автоматически закрывает соединение после завершения запроса."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Создает таблицу в SQLite, если её нет."""
    # Используем отдельное подключение, так как это запускается вне контекста Flask
    conn = sqlite3.connect(DB_PATH, timeout=15)
    with conn:
        conn.execute("PRAGMA journal_mode=WAL;")  # Включаем параллельный режим работы
        conn.execute("PRAGMA synchronous = NORMAL;") # Ускоряет запись в WAL
        conn.execute('''
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT,
                address TEXT,
                category TEXT,
                text_message TEXT,
                media_path TEXT,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    conn.close()

try:
    init_db()
except Exception as e:
    print(f"Ошибка при инициализации БД: {e}")

# ==========================================
# БЛОК БЕЗОПАСНОСТИ
# ==========================================
@app.before_request
def require_login():
    allowed_routes = ['login', 'static']
    if request.endpoint not in allowed_routes and 'logged_in' not in session:
        return redirect(url_for('login'))

# ==========================================
# СЛОВАРИ И HTML ШАБЛОНЫ (Оставлены без изменений для экономии места)
# ==========================================
TRANSLATIONS = {
    # ... твой словарь TRANSLATIONS ...
}

LOGIN_TEMPLATE = """
    """

HTML_TEMPLATE = """
    """

# ==========================================
# МАРШРУТЫ
# ==========================================
@app.errorhandler(500)
def internal_server_error(e):
    error_trace = traceback.format_exc()
    print("CRITICAL ERROR:", error_trace)
    return f"""
    <div style="padding: 40px; font-family: sans-serif; max-width: 800px; margin: auto;">
        <h1 style="color: #dc2626;">Произошла ошибка 500 😱</h1>
        <pre style="background: #f1f5f9; padding: 20px; border-radius: 8px; overflow-x: auto; font-size: 14px;">{error_trace}</pre>
    </div>
    """, 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USERNAME and request.form.get('password') == ADMIN_PASSWORD:
            session.permanent = True # Активируем время жизни сессии
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = "Неверный логин или пароль"
    return render_template_string(LOGIN_TEMPLATE, error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/view_media/<int:c_id>')
def view_media(c_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT media_path FROM complaints WHERE id = ?", (c_id,))
    row = cursor.fetchone()
    
    if row and row['media_path']:
        full_path = os.path.join(BASE_DIR, row['media_path'])
        directory, filename = os.path.split(full_path)
        return send_from_directory(directory, filename)
    return "Файл не найден", 404

@app.route('/')
def index():
    lang = request.args.get('lang', 'ru')
    filter_type = request.args.get('filter', 'all')
    t = TRANSLATIONS.get(lang, TRANSLATIONS['ru'])
    
    db = get_db()
    cursor = db.cursor()
    
    if filter_type == 'trash':
        cursor.execute("SELECT * FROM complaints WHERE status = 'trash' ORDER BY created_at DESC")
    elif filter_type in ['complaint', 'suggestion']:
        cursor.execute("SELECT * FROM complaints WHERE status != 'trash' AND category = ? ORDER BY created_at DESC", (filter_type,))
    else: 
        cursor.execute("SELECT * FROM complaints WHERE status != 'trash' ORDER BY created_at DESC")
    
    complaints_raw = cursor.fetchall()
    
    complaints = []
    for row in complaints_raw:
        item = dict(row)
        if item.get('created_at'):
            try:
                clean_date = item['created_at'].split('.')[0]
                item['created_at'] = datetime.strptime(clean_date, '%Y-%m-%d %H:%M:%S')
            except Exception:
                pass
        complaints.append(item)
    
    cursor.execute("SELECT status, count(*) FROM complaints WHERE status != 'trash' GROUP BY status")
    stats_raw = dict(cursor.fetchall())
    stats = {
        'total': sum(stats_raw.values()),
        'new': stats_raw.get('new', 0),
        'in_progress': stats_raw.get('in_progress', 0),
        'resolved': stats_raw.get('resolved', 0)
    }
    
    return render_template_string(HTML_TEMPLATE, complaints=complaints, t=t, lang=lang, filter_type=filter_type, stats=stats)

@app.route('/action/<int:c_id>/<action>')
def handle_action(c_id, action):
    lang = request.args.get('lang', 'ru')
    filter_type = request.args.get('filter', 'all')
    db = get_db()
    cursor = db.cursor()

    if action == 'delete_permanent':
        cursor.execute("DELETE FROM complaints WHERE id = ?", (c_id,))
    else:
        new_status = 'trash' if action == 'trash' else 'new'
        cursor.execute("UPDATE complaints SET status = ? WHERE id = ?", (new_status, c_id))
        
    db.commit()
    return redirect(url_for('index', lang=lang, filter=filter_type))

@app.route('/update_status/<int:c_id>')
def update_status(c_id):
    new_status = request.args.get('status', 'new')
    lang = request.args.get('lang', 'ru')
    filter_type = request.args.get('filter', 'all')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE complaints SET status = ? WHERE id = ?", (new_status, c_id))
    db.commit()
    
    return redirect(url_for('index', lang=lang, filter=filter_type))

@app.route('/export', methods=['POST'])
def export():
    lang = request.form.get('lang', 'ru')
    selected_ids = request.form.getlist('selected_ids')
    
    if not selected_ids:
        return redirect(url_for('index', lang=lang))
        
    selected_ids = [int(id) for id in selected_ids]
    placeholders = ','.join('?' for _ in selected_ids)
    
    query = f"""
        SELECT 
            created_at as "Дата и время",
            phone as "Номер телефона",
            address as "Адрес маркета",
            category as "Категория",
            status as raw_status,
            text_message as "Текст",
            media_path as "Путь к файлу"
        FROM complaints 
        WHERE id IN ({placeholders}) 
        ORDER BY created_at DESC
    """
    
    # ОПТИМИЗАЦИЯ: Pandas отлично работает напрямую с sqlite3 connection
    db = get_db()
    df = pd.read_sql_query(query, db, params=selected_ids)
    
    status_dict = {'new': 'Новая', 'in_progress': 'В работе', 'under_review': 'На согласовании', 'resolved': 'Решена', 'closed': 'Закрыта', 'trash': 'В корзине'}
    category_dict = {'complaint': 'Жалоба', 'suggestion': 'Предложение'}
    
    if 'raw_status' in df.columns:
        df.insert(4, 'Статус', df['raw_status'].map(status_dict))
        df = df.drop(columns=['raw_status'])
        
    if 'Категория' in df.columns:
        df['Категория'] = df['Категория'].map(category_dict)
        
    if 'Дата и время' in df.columns:
        df['Дата и время'] = pd.to_datetime(df['Дата и время']).dt.strftime('%Y-%m-%d %H:%M')
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Выбранные заявки')
    
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="Отчет_CRM.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == '__main__':
    print("🌐 CRM система успешно запущена!")
    port = int(os.environ.get("PORT", 5000))
    # ОПТИМИЗАЦИЯ: Включаем многопоточность для waitress (по умолчанию она есть, но лучше задать явно)
    serve(app, host='0.0.0.0', port=port, threads=8)