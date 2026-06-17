import sqlite3
import pandas as pd
import os
import logging
from datetime import datetime
from contextlib import closing

# ========================================================
# НАСТРОЙКИ ДЛЯ VDS
# ========================================================

# 1. Вычисляем абсолютный путь к директории скрипта
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "complaints.db")
LOG_FILE = os.path.join(BASE_DIR, "script_actions.log")

# 2. Настраиваем логирование (запись в файл + вывод в консоль)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler() # Оставляет вывод в консоль, если запускаешь руками
    ]
)

def get_db_connection():
    """Создает подключение к БД с нужными настройками таймаута."""
    conn = sqlite3.connect(DB_NAME, timeout=15)
    # На всякий случай включаем WAL, если скрипт запускается параллельно с Flask
    conn.execute("PRAGMA journal_mode=WAL;") 
    return conn

# ========================================================
# ФУНКЦИИ
# ========================================================

def export_to_excel(output_filename="отчет_жалобы.xlsx"):
    """Выгружает данные из базы в Excel-файл с правильным выбором колонок."""
    
    # Делаем путь к файлу абсолютным, чтобы он не сохранился в корень сервера
    export_path = os.path.join(BASE_DIR, output_filename)
    
    try:
        with closing(get_db_connection()) as conn:
            query = """
                SELECT 
                    id as 'ID Заявки',
                    created_at as 'Дата создания',
                    phone as 'Номер телефона',
                    address as 'Адрес',
                    category as 'Категория',
                    text_message as 'Текст обращения',
                    media_path as 'Медиафайл',
                    status as 'Статус'
                FROM complaints 
                ORDER BY created_at DESC
            """
            # Примечание: Убрал колонки language и priority, так как их не было 
            # в таблице complaints из предыдущего кода. Верни их в SQL запрос, 
            # если они реально добавлены в структуру твоей БД.
            
            df = pd.read_sql_query(query, conn)
            
            if df.empty:
                logging.info("База данных пуста. Отчет не сформирован.")
                return
            
            df.to_excel(export_path, index=False)
            logging.info(f"Отчет успешно сформирован: {export_path}")
            
    except Exception as e:
        logging.error(f"Ошибка при экспорте в Excel: {e}")

def change_status(complaint_id, new_status, admin_name="admin_script"):
    """Меняет статус обращения и записывает действие в историю."""
    try:
        with closing(get_db_connection()) as conn:
            with conn:  # Менеджер контекста для авто-commit
                cursor = conn.cursor()
                
                cursor.execute("SELECT status FROM complaints WHERE id = ?", (complaint_id,))
                row = cursor.fetchone()
                
                if not row:
                    logging.warning(f"Обращение с ID {complaint_id} не найдено в БД.")
                    return
                
                old_status = row[0]
                if old_status == new_status:
                    logging.info(f"Статус обращения {complaint_id} уже '{new_status}'.")
                    return

                now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                
                # Обновляем статус 
                # (убрал updated_at, так как его не было в схеме БД из первого скрипта. 
                # Если добавил в БД - раскомментируй)
                cursor.execute(
                    "UPDATE complaints SET status = ? WHERE id = ?", 
                    # "UPDATE complaints SET status = ?, updated_at = ? WHERE id = ?", 
                    (new_status, complaint_id)
                    # (new_status, now, complaint_id)
                )
                
                # Проверка: существует ли таблица истории вообще? Если это новый функционал, 
                # нужно убедиться, что таблица complaint_history создана.
                cursor.execute(
                    """INSERT INTO complaint_history 
                       (complaint_id, changed_by, action, old_value, new_value, changed_at) 
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (complaint_id, admin_name, 'status_change', old_status, new_status, now)
                )
                
                logging.info(f"Статус заявки №{complaint_id} изменен: '{old_status}' -> '{new_status}'.")
                
    except sqlite3.OperationalError as e:
        logging.error(f"Ошибка БД (возможно нет таблицы complaint_history или updated_at): {e}")
    except Exception as e:
        logging.error(f"Неизвестная ошибка при изменении статуса: {e}")

# ========================================================
# БЛОК ЗАПУСКА
# ========================================================
if __name__ == '__main__':
    logging.info("Скрипт управления БД запущен.")
    
    # Выгрузка отчета
    # export_to_excel()
    
    # Пример смены статуса
    # change_status(complaint_id=1, new_status="in_progress")