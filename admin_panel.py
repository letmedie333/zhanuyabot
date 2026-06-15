import sqlite3
import pandas as pd
from flask import Flask, render_template_string, request, redirect, url_for, send_file
from io import BytesIO
from flask import send_from_directory
import os
from dotenv import load_dotenv
from waitress import serve
from flask import Response
import traceback  # Добавлено для отслеживания ошибок

app = Flask(__name__)
DB_NAME = "complaints.db"

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (ФИКС ОШИБКИ 500) ---
def init_db():
    """Создает таблицу, если её нет. Защищает от ошибки 500 на чистом сервере Render."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            address TEXT,
            category TEXT,
            text_message TEXT,
            media_path TEXT,
            status TEXT DEFAULT 'new',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Запускаем проверку базы при старте
init_db()
# ---------------------------------------------------

# --- БЛОК БЕЗОПАСНОСТИ ---
load_dotenv()
ADMIN_USERNAME = os.getenv("CRM_USER", "admin")
ADMIN_PASSWORD = os.getenv("CRM_PASS", "12345")

def check_auth(username, password):
    """Проверяет логин и пароль."""
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    """Отправляет запрос на ввод пароля."""
    return Response(
        'Вход только для сотрудников Жанұя.\nПожалуйста, авторизуйтесь.', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

@app.before_request
def require_login():
    """Требует пароль для ЛЮБОЙ страницы CRM."""
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return authenticate()
# -------------------------

# ==========================================
# СЛОВАРИ ПЕРЕВОДОВ (RU / KZ)
# ==========================================
TRANSLATIONS = {
    'ru': {
        'nav_title': 'CRM | Жанұя',
        'btn_export': '📥 Выгрузить Excel',
        'tab_all': 'Все активные',
        'tab_complaints': 'Жалобы',
        'tab_suggestions': 'Предложения',
        'tab_trash': 'Корзина',
        'search_placeholder': 'Поиск по телефону, тексту, адресу...',
        'stat_total': 'Всего активных',
        'stat_new': 'Новые',
        'stat_in_progress': 'В работе',
        'stat_resolved': 'Решены',
        'col_select': 'Выбор',
        'col_date': 'Дата',
        'col_phone': 'Телефон',
        'col_address': 'Маркет',
        'col_type': 'Тип',
        'col_status': 'Статус',
        'col_action': 'Действия',
        'badge_complaint': 'Жалоба',
        'badge_suggestion': 'Предложение',
        'has_file': '📎 Файл',
        'no_file': 'Нет',
        'btn_trash': 'В мусор',
        'btn_restore': 'Восстановить',
        'btn_delete_perm': 'Удалить навсегда',
        'btn_details': 'Детали',
        'empty_msg': 'В этой категории пока нет обращений.',
        'switch_lang_name': 'Қазақ тілі',
        'switch_lang_code': 'kz',
        'alert_select': 'Пожалуйста, выберите хотя бы одно обращение!',
        'modal_title': 'Карточка обращения',
        'modal_close': 'Закрыть',
        'st_new': 'Новая',
        'st_in_progress': 'В работе',
        'st_under_review': 'На согласовании',
        'st_resolved': 'Решена',
        'st_closed': 'Закрыта',
        'st_trash': 'В корзине'
    },
    'kz': {
        'nav_title': 'CRM | Жанұя',
        'btn_export': '📥 Excel жүктеу',
        'tab_all': 'Барлығы',
        'tab_complaints': 'Шағымдар',
        'tab_suggestions': 'Ұсыныстар',
        'tab_trash': 'Себет',
        'search_placeholder': 'Телефон, мәтін, мекенжай бойынша іздеу...',
        'stat_total': 'Барлық белсенді',
        'stat_new': 'Жаңа',
        'stat_in_progress': 'Жұмыста',
        'stat_resolved': 'Шешілді',
        'col_select': 'Таңдау',
        'col_date': 'Күні',
        'col_phone': 'Телефон',
        'col_address': 'Маркет',
        'col_type': 'Түрі',
        'col_status': 'Мәртебесі',
        'col_action': 'Әрекет',
        'badge_complaint': 'Шағым',
        'badge_suggestion': 'Ұсыныс',
        'has_file': '📎 Файл',
        'no_file': 'Жоқ',
        'btn_trash': 'Себетке',
        'btn_restore': 'Қалпына келтіру',
        'btn_delete_perm': 'Толығымен жою',
        'btn_details': 'Толығырақ',
        'empty_msg': 'Бұл санатта әзірге өтініштер жоқ.',
        'switch_lang_name': 'Русский',
        'switch_lang_code': 'ru',
        'alert_select': 'Кем дегенде бір өтінішті таңдаңыз!',
        'modal_title': 'Өтініш мәліметтері',
        'modal_close': 'Жабу',
        'st_new': 'Жаңа',
        'st_in_progress': 'Жұмыста',
        'st_under_review': 'Келісуде',
        'st_resolved': 'Шешілді',
        'st_closed': 'Жабық',
        'st_trash': 'Себетте'
    }
}

# ==========================================
# HTML ДИЗАЙН (СОВРЕМЕННЫЙ UI)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Жанұя CRM</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --orange: #f97316;
            --orange-hover: #ea580c;
            --green: #16a34a;
            --green-hover: #15803d;
            --dark: #1e293b;
            --bg: #f8fafc;
            --surface: #ffffff;
            --border: #e2e8f0;
            --text: #334155;
        }
        
        body { font-family: 'Inter', sans-serif; background-color: var(--bg); margin: 0; color: var(--text); }
        
        /* Навигация */
        nav { background-color: var(--surface); border-bottom: 3px solid var(--orange); padding: 15px 40px; position: sticky; top: 0; z-index: 1000; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        nav h1 { margin: 0; font-size: 24px; font-weight: 700; color: var(--green); display: flex; align-items: center; gap: 8px;}
        nav h1 span { color: var(--orange); }
        
        .header-controls { display: flex; gap: 15px; align-items: center; }
        .lang-switch { color: var(--text); text-decoration: none; font-weight: 600; padding: 8px 16px; border-radius: 8px; font-size: 14px; transition: 0.2s; border: 1px solid var(--border); }
        .lang-switch:hover { background-color: var(--bg); border-color: var(--green); color: var(--green); }
        
        .btn-export { background-color: var(--green); color: white; padding: 10px 20px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; transition: 0.2s; font-size: 14px; box-shadow: 0 4px 6px rgba(22, 163, 74, 0.2); }
        .btn-export:hover { background-color: var(--green-hover); transform: translateY(-1px); }

        .container { padding: 30px 40px; max-width: 1600px; margin: auto; }

        /* Карточки статистики */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: var(--surface); padding: 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); border: 1px solid var(--border); border-left: 4px solid var(--orange); display: flex; flex-direction: column; }
        .stat-card:nth-child(2) { border-left-color: #3b82f6; }
        .stat-card:nth-child(3) { border-left-color: #eab308; }
        .stat-card:nth-child(4) { border-left-color: var(--green); }
        .stat-card span { font-size: 13px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-card strong { font-size: 28px; font-weight: 700; color: var(--dark); margin-top: 5px; }

        /* Тулбар (Фильтры и Поиск) */
        .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; gap: 20px; flex-wrap: wrap;}
        .tabs { display: flex; gap: 8px; background: var(--surface); padding: 5px; border-radius: 10px; border: 1px solid var(--border); }
        .tab { padding: 8px 16px; color: var(--text); text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 14px; transition: 0.2s; }
        .tab:hover { background: var(--bg); }
        .tab.active { background: var(--orange); color: white; box-shadow: 0 2px 4px rgba(249, 115, 22, 0.2);}
        .tab.trash { color: #ef4444; }
        .tab.trash.active { background: #ef4444; color: white; }

        .search-box { flex-grow: 1; max-width: 400px; position: relative; }
        .search-box input { width: 100%; padding: 10px 15px 10px 35px; border-radius: 8px; border: 1px solid var(--border); outline: none; font-size: 14px; font-family: 'Inter'; transition: 0.2s; box-sizing: border-box;}
        .search-box input:focus { border-color: var(--green); box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.1); }
        .search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); opacity: 0.5; }

        /* Таблица */
        .table-container { background: var(--surface); border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.03); border: 1px solid var(--border); overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; white-space: nowrap; }
        th, td { padding: 15px 20px; text-align: left; border-bottom: 1px solid var(--border); font-size: 14px; vertical-align: middle; }
        th { background-color: #f8fafc; color: #475569; font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }
        
        /* Кликабельная строка */
        .clickable-row { cursor: pointer; transition: background-color 0.15s; }
        .clickable-row:hover { background-color: #fff7ed; } /* Легкий оранжевый оттенок при наведении */
        tr:last-child td { border-bottom: none; }
        
        .badge { padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; gap: 5px;}
        .badge-complaint { background-color: #ffedd5; color: var(--orange-hover); border: 1px solid #fed7aa; }
        .badge-suggestion { background-color: #dcfce7; color: var(--green-hover); border: 1px solid #bbf7d0; }
        
        .status-select { padding: 6px 30px 6px 12px; border-radius: 8px; font-weight: 600; font-size: 13px; border: 1px solid transparent; cursor: pointer; outline: none; transition: 0.2s; appearance: none; background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e"); background-repeat: no-repeat; background-position: right 8px center; background-size: 14px; font-family: 'Inter';}
        .status-select:hover { filter: brightness(0.95); }
        .status-new { background-color: #e0f2fe; color: #0284c7; border-color: #bae6fd; }
        .status-in_progress { background-color: #fef08a; color: #a16207; border-color: #fde047; }
        .status-under_review { background-color: #f3e8ff; color: #7e22ce; border-color: #e9d5ff; }
        .status-resolved { background-color: #dcfce7; color: var(--green); border-color: #bbf7d0; }
        .status-closed { background-color: #f1f5f9; color: #475569; border-color: #cbd5e1; }

        .btn-action { padding: 8px 12px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; text-decoration: none; font-size: 13px; display: inline-flex; align-items: center; justify-content: center; transition: 0.2s;}
        .btn-action.trash { background-color: var(--bg); color: #64748b; border: 1px solid var(--border);}
        .btn-action.trash:hover { background-color: #fee2e2; color: #dc2626; border-color: #fca5a5;}
        .btn-action.restore { background-color: var(--green); color: white; }
        .btn-action.restore:hover { background-color: var(--green-hover); }
        .btn-action.delete { background-color: #dc2626; color: white; margin-left: 5px; }
        .btn-action.delete:hover { background-color: #b91c1c; }
        
        .empty-state { text-align: center; padding: 60px; color: #94a3b8; font-size: 15px; }

        /* Модальное окно */
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(15, 23, 42, 0.6); z-index: 2000; justify-content: center; align-items: center; backdrop-filter: blur(4px);}
        .modal { background: white; padding: 0; border-radius: 16px; width: 600px; max-width: 90%; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25); overflow: hidden; display: flex; flex-direction: column; max-height: 90vh; border-top: 4px solid var(--orange);}
        .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 20px 25px; border-bottom: 1px solid var(--border); background: #f8fafc;}
        .modal-header h2 { margin: 0; font-size: 18px; color: var(--dark); font-weight: 700;}
        .modal-close { cursor: pointer; font-size: 24px; color: #94a3b8; border: none; background: none; transition: 0.2s;}
        .modal-close:hover { color: #dc2626; }
        .modal-body { padding: 25px; overflow-y: auto; font-size: 15px; color: var(--text); }
        .modal-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }
        .m-field { display: flex; flex-direction: column; gap: 4px; }
        .m-label { font-size: 12px; color: #64748b; font-weight: 600; text-transform: uppercase; }
        .m-value { font-weight: 600; color: var(--dark); }
        .chat-bubble { background: #fff7ed; padding: 15px; border-radius: 12px; font-style: normal; white-space: pre-wrap; line-height: 1.6; border: 1px solid #ffedd5; color: var(--dark);}
        .media-preview { margin-top: 15px; text-align: center; background: #f1f5f9; border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
        .media-preview img, .media-preview video { max-width: 100%; height: auto; display: block; margin: 0 auto;}
        
        .btn-whatsapp { background-color: var(--green); color: white; border-radius: 8px; text-decoration: none; font-weight: 600; display: flex; justify-content: center; align-items: center; padding: 12px; margin-top: 20px; transition: 0.2s;}
        .btn-whatsapp:hover { background-color: var(--green-hover); transform: translateY(-1px);}
    </style>
    <script>
        function filterTable() {
            let input = document.getElementById("searchInput").value.toLowerCase();
            let table = document.getElementById("complaintsTable");
            let tr = table.getElementsByTagName("tr");

            for (let i = 1; i < tr.length; i++) {
                let tdArray = tr[i].getElementsByTagName("td");
                let rowText = "";
                for(let j = 1; j < 5; j++) {
                    if(tdArray[j]) rowText += tdArray[j].innerText.toLowerCase() + " ";
                }
                
                if (rowText.includes(input)) {
                    tr[i].style.display = "";
                } else {
                    tr[i].style.display = "none";
                }
            }
        }

        function toggleAll(source) {
            let checkboxes = document.getElementsByName('selected_ids');
            for(let i=0, n=checkboxes.length; i<n; i++) {
                checkboxes[i].checked = source.checked;
            }
        }
        
        function validateExport(event) {
            let checked = document.querySelectorAll('input[name="selected_ids"]:checked').length;
            if (checked === 0) {
                alert("{{ t.alert_select }}");
                event.preventDefault();
            }
        }

        // Обработчик клика по строке (чтобы не открывать окно, если кликнули на селект или чекбокс)
        function handleRowClick(event, date, phone, address, type, text, mediaLink) {
            // Игнорируем клики по интерактивным элементам внутри строки
            const targetTag = event.target.tagName;
            if (['INPUT', 'SELECT', 'OPTION', 'A', 'BUTTON'].includes(targetTag)) {
                return;
            }
            openModal(date, phone, address, type, text, mediaLink);
        }

        function openModal(date, phone, address, type, text, mediaLink) {
            document.getElementById('m-date').innerText = date;
            document.getElementById('m-phone').innerText = phone;
            document.getElementById('m-address').innerText = address;
            document.getElementById('m-type').innerText = type;
            
            document.getElementById('m-text').innerText = text ? text : "Без текста";
            
            let cleanPhone = phone.replace(/\\D/g,'');
            document.getElementById('modal-whatsapp').href = "https://wa.me/" + cleanPhone;
            
            let mediaContainer = document.getElementById('modal-media');
            if (mediaLink && mediaLink !== "None" && mediaLink.trim() !== "") {
                let ext = mediaLink.split('=').pop().toLowerCase();
                if (['jpg', 'jpeg', 'png'].includes(ext)) {
                    mediaContainer.innerHTML = `<div class="media-preview"><img src="${mediaLink}" alt="Вложение"></div>`;
                } else if (['mp4'].includes(ext)) {
                    mediaContainer.innerHTML = `<div class="media-preview"><video controls src="${mediaLink}" style="width: 100%;"></video></div>`;
                } else if (['ogg', 'mp3', 'aac', 'amr', 'm4a'].includes(ext)) {
                    mediaContainer.innerHTML = `<div class="media-preview" style="padding: 15px;"><audio controls style="width: 100%;" src="${mediaLink}"></audio></div>`;
                } else {
                    mediaContainer.innerHTML = `<div style="margin-top: 15px; text-align: center;"><a href="${mediaLink}" target="_blank" style="color: var(--orange); font-weight: bold;">Скачать вложение</a></div>`;
                }
            } else {
                mediaContainer.innerHTML = '';
            }
            
            document.getElementById('detailsModal').style.display = 'flex';
        }

        function closeModal() {
            document.getElementById('detailsModal').style.display = 'none';
        }
    </script>
</head>
<body>

    <form action="/export" method="POST" onsubmit="validateExport(event)">
        <input type="hidden" name="lang" value="{{ lang }}">
        
        <nav>
            <h1><span>Жанұя</span> CRM</h1>
            <div class="header-controls">
                <a href="/?lang={{ t.switch_lang_code }}&filter={{ filter_type }}" class="lang-switch">{{ t.switch_lang_name }}</a>
                <button type="submit" class="btn-export">{{ t.btn_export }}</button>
            </div>
        </nav>

        <div class="container">
            
            <!-- Дашборд -->
            {% if filter_type != 'trash' %}
            <div class="stats-grid">
                <div class="stat-card">
                    <span>{{ t.stat_total }}</span>
                    <strong>{{ stats.total }}</strong>
                </div>
                <div class="stat-card">
                    <span>{{ t.stat_new }}</span>
                    <strong style="color: #0284c7;">{{ stats.new }}</strong>
                </div>
                <div class="stat-card">
                    <span>{{ t.stat_in_progress }}</span>
                    <strong style="color: #d97706;">{{ stats.in_progress }}</strong>
                </div>
                <div class="stat-card">
                    <span>{{ t.stat_resolved }}</span>
                    <strong style="color: var(--green);">{{ stats.resolved }}</strong>
                </div>
            </div>
            {% endif %}

            <div class="toolbar">
                <div class="tabs">
                    <a href="/?lang={{ lang }}&filter=all" class="tab {% if filter_type == 'all' %}active{% endif %}">{{ t.tab_all }}</a>
                    <a href="/?lang={{ lang }}&filter=complaint" class="tab {% if filter_type == 'complaint' %}active{% endif %}">{{ t.tab_complaints }}</a>
                    <a href="/?lang={{ lang }}&filter=suggestion" class="tab {% if filter_type == 'suggestion' %}active{% endif %}">{{ t.tab_suggestions }}</a>
                    <a href="/?lang={{ lang }}&filter=trash" class="tab trash {% if filter_type == 'trash' %}active{% endif %}">{{ t.tab_trash }}</a>
                </div>
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input type="text" id="searchInput" onkeyup="filterTable()" placeholder="{{ t.search_placeholder }}">
                </div>
            </div>

            <div class="table-container">
                <table id="complaintsTable">
                    <thead>
                        <tr>
                            <th style="width: 40px; text-align: center;"><input type="checkbox" onClick="toggleAll(this)"></th>
                            <th>{{ t.col_date }}</th>
                            <th>{{ t.col_phone }}</th>
                            <th>{{ t.col_address }}</th>
                            <th>{{ t.col_type }}</th>
                            <th>{{ t.col_status }}</th>
                            <th style="text-align: right;">{{ t.col_action }}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in complaints %}
                        <!-- Строка теперь кликабельная -->
                        <tr class="clickable-row" onclick="handleRowClick(event, '{{ row.created_at[:16] }}', '{{ row.phone }}', '{{ row.address }}', '{{ t.badge_complaint if row.category == 'complaint' else t.badge_suggestion }}', '{{ row.text_message | replace('\n', '\\n') | escape }}', '{% if row.media_path %}/view_media/{{ row.id }}?ext={{ row.media_path.split('.')[-1] }}{% endif %}')">
                            <td style="text-align: center;"><input type="checkbox" name="selected_ids" value="{{ row.id }}"></td>
                            <td style="color: #64748b; font-weight: 500;">{{ row.created_at[:16] }}</td>
                            <td style="font-weight: 600;">{{ row.phone }}</td>
                            <td style="color: #475569;">{{ row.address }}</td>
                            <td>
                                <span class="badge {% if row.category == 'complaint' %}badge-complaint{% else %}badge-suggestion{% endif %}">
                                    {{ t.badge_complaint if row.category == 'complaint' else t.badge_suggestion }}
                                </span>
                            </td>
                            <td>
                                {% if row.status == 'trash' %}
                                    <span class="badge" style="background-color: #f1f5f9; color: #94a3b8;">{{ t.st_trash }}</span>
                                {% else %}
                                    <select onchange="window.location.href='/update_status/{{ row.id }}?status=' + this.value + '&lang={{ lang }}&filter={{ filter_type }}'" class="status-select status-{{ row.status }}">
                                        <option value="new" {% if row.status == 'new' %}selected{% endif %}>{{ t.st_new }}</option>
                                        <option value="in_progress" {% if row.status == 'in_progress' %}selected{% endif %}>{{ t.st_in_progress }}</option>
                                        <option value="under_review" {% if row.status == 'under_review' %}selected{% endif %}>{{ t.st_under_review }}</option>
                                        <option value="resolved" {% if row.status == 'resolved' %}selected{% endif %}>{{ t.st_resolved }}</option>
                                        <option value="closed" {% if row.status == 'closed' %}selected{% endif %}>{{ t.st_closed }}</option>
                                    </select>
                                {% endif %}
                            </td>
                            <td style="text-align: right; display: flex; gap: 8px; justify-content: flex-end;">
                                {% if row.status == 'trash' %}
                                    <!-- Кнопки восстановления и ПОЛНОГО удаления в корзине -->
                                    <a href="/action/{{ row.id }}/restore?lang={{ lang }}&filter={{ filter_type }}" class="btn-action restore">{{ t.btn_restore }}</a>
                                    <a href="/action/{{ row.id }}/delete_permanent?lang={{ lang }}&filter={{ filter_type }}" class="btn-action delete" onclick="return confirm('Точно удалить навсегда? Это действие нельзя отменить.');">{{ t.btn_delete_perm }}</a>
                                {% else %}
                                    <!-- Кнопка удаления в корзину -->
                                    <a href="/action/{{ row.id }}/trash?lang={{ lang }}&filter={{ filter_type }}" class="btn-action trash" title="{{ t.btn_trash }}">🗑️</a>
                                {% endif %}
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="7" class="empty-state">{{ t.empty_msg }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </form>

    <!-- Модальное окно -->
    <div id="detailsModal" class="modal-overlay" onclick="if(event.target==this) closeModal()">
        <div class="modal">
            <div class="modal-header">
                <h2>{{ t.modal_title }}</h2>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="modal-grid">
                    <div class="m-field">
                        <span class="m-label">Дата</span>
                        <span class="m-value" id="m-date"></span>
                    </div>
                    <div class="m-field">
                        <span class="m-label">Тип</span>
                        <span class="m-value" id="m-type"></span>
                    </div>
                    <div class="m-field">
                        <span class="m-label">Телефон</span>
                        <span class="m-value" id="m-phone"></span>
                    </div>
                    <div class="m-field">
                        <span class="m-label">Маркет</span>
                        <span class="m-value" id="m-address"></span>
                    </div>
                </div>
                
                <div class="m-label" style="margin-bottom: 8px;">Текст обращения</div>
                <div class="chat-bubble" id="m-text"></div>
                
                <div id="modal-media"></div>
                
                <a id="modal-whatsapp" href="#" target="_blank" class="btn-whatsapp">
                    💬 Ответить в WhatsApp
                </a>
            </div>
        </div>
    </div>

</body>
</html>
"""

# ==========================================
# ПЕРЕХВАТЧИК ОШИБОК 500
# ==========================================
@app.errorhandler(500)
def internal_server_error(e):
    """Отлавливает все ошибки на сервере и выводит их на экран вместо 'Белого экрана смерти'."""
    error_trace = traceback.format_exc()
    print("CRITICAL ERROR:", error_trace)  # Пишет в логи Render
    return f"""
    <div style="padding: 40px; font-family: sans-serif; max-width: 800px; margin: auto;">
        <h1 style="color: #dc2626;">Произошла ошибка 500 😱</h1>
        <p>Сервер столкнулся с внутренней ошибкой. Передай этот текст разработчику:</p>
        <pre style="background: #f1f5f9; padding: 20px; border-radius: 8px; overflow-x: auto; font-size: 14px;">{error_trace}</pre>
    </div>
    """, 500

# ==========================================
# МАРШРУТЫ (РОУТЫ) ПРИЛОЖЕНИЯ
# ==========================================
@app.route('/view_media/<int:c_id>')
def view_media(c_id):
    """Находит файл в базе и отдает его для просмотра"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT media_path FROM complaints WHERE id = ?", (c_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0]:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        full_path = os.path.join(base_dir, row[0])
        directory, filename = os.path.split(full_path)
        return send_from_directory(directory, filename)
    return "Файл не найден", 404

@app.route('/')
def index():
    lang = request.args.get('lang', 'ru')
    filter_type = request.args.get('filter', 'all')
    
    t = TRANSLATIONS.get(lang, TRANSLATIONS['ru'])
    
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Запрашиваем жалобы
    if filter_type == 'trash':
        cursor.execute("SELECT * FROM complaints WHERE status = 'trash' ORDER BY created_at DESC")
    elif filter_type in ['complaint', 'suggestion']:
        cursor.execute("SELECT * FROM complaints WHERE status != 'trash' AND category = ? ORDER BY created_at DESC", (filter_type,))
    else: 
        cursor.execute("SELECT * FROM complaints WHERE status != 'trash' ORDER BY created_at DESC")
    complaints = cursor.fetchall()
    
    # Собираем статистику
    cursor.execute("SELECT status, count(*) FROM complaints WHERE status != 'trash' GROUP BY status")
    stats_raw = dict(cursor.fetchall())
    stats = {
        'total': sum(stats_raw.values()),
        'new': stats_raw.get('new', 0),
        'in_progress': stats_raw.get('in_progress', 0),
        'resolved': stats_raw.get('resolved', 0)
    }
    
    conn.close()
    
    return render_template_string(HTML_TEMPLATE, complaints=complaints, t=t, lang=lang, filter_type=filter_type, stats=stats)

@app.route('/action/<int:c_id>/<action>')
def handle_action(c_id, action):
    lang = request.args.get('lang', 'ru')
    filter_type = request.args.get('filter', 'all')
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if action == 'delete_permanent':
        cursor.execute("DELETE FROM complaints WHERE id = ?", (c_id,))
    else:
        new_status = 'trash' if action == 'trash' else 'new'
        cursor.execute("UPDATE complaints SET status = ? WHERE id = ?", (new_status, c_id))
        
    conn.commit()
    conn.close()
    
    return redirect(url_for('index', lang=lang, filter=filter_type))

@app.route('/update_status/<int:c_id>')
def update_status(c_id):
    new_status = request.args.get('status', 'new')
    lang = request.args.get('lang', 'ru')
    filter_type = request.args.get('filter', 'all')
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE complaints SET status = ? WHERE id = ?", (new_status, c_id))
    conn.commit()
    conn.close()
    
    return redirect(url_for('index', lang=lang, filter=filter_type))

@app.route('/export', methods=['POST'])
def export():
    lang = request.form.get('lang', 'ru')
    selected_ids = request.form.getlist('selected_ids')
    
    if not selected_ids:
        return redirect(url_for('index', lang=lang))
        
    placeholders = ','.join('?' for _ in selected_ids)
    
    query = f"""
        SELECT 
            created_at as 'Дата и время',
            phone as 'Номер телефона',
            address as 'Адрес маркета',
            category as 'Категория',
            status as 'raw_status',
            text_message as 'Текст',
            media_path as 'Путь к файлу'
        FROM complaints 
        WHERE id IN ({placeholders}) 
        ORDER BY created_at DESC
    """
    
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(query, conn, params=selected_ids)
    conn.close()
    
    status_dict = {
        'new': 'Новая', 'in_progress': 'В работе',
        'under_review': 'На согласовании', 'resolved': 'Решена',
        'closed': 'Закрыта', 'trash': 'В корзине'
    }
    category_dict = {
        'complaint': 'Жалоба', 'suggestion': 'Предложение'
    }
    
    if 'raw_status' in df.columns:
        df.insert(4, 'Статус', df['raw_status'].map(status_dict))
        df = df.drop(columns=['raw_status'])
        
    if 'Категория' in df.columns:
        df['Категория'] = df['Категория'].map(category_dict)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Выбранные заявки')
    
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name="Отчет_CRM.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if __name__ == '__main__':
    print("🌐 CRM система Жанұя запущена (ЗАЩИЩЕННЫЙ РЕЖИМ)!")
    print("👉 Локальная ссылка: http://127.0.0.1:5000")
    # Используем waitress для продакшена вместо встроенного сервера Flask
    port = int(os.environ.get("PORT", 5000))
    serve(app, host='0.0.0.0', port=port)