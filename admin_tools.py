import sqlite3
import pandas as pd
from datetime import datetime
from contextlib import closing

DB_NAME = "complaints.db"

def export_to_excel(output_filename="отчет_жалобы.xlsx"):
    """Выгружает данные из базы в Excel-файл с правильным выбором колонок."""
    try:
        with closing(sqlite3.connect(DB_NAME, timeout=10)) as conn:
            # Используем SQL-запрос для выбора нужных колонок и их переименования
            query = """
                SELECT 
                    id as 'ID Заявки',
                    created_at as 'Дата создания',
                    phone as 'Номер телефона',
                    language as 'Язык',
                    address as 'Адрес',
                    category as 'Категория',
                    text_message as 'Текст обращения',
                    media_path as 'Медиафайл',
                    status as 'Статус',
                    priority as 'Приоритет'
                FROM complaints 
                ORDER BY created_at DESC
            """
            df = pd.read_sql_query(query, conn)
            
            if df.empty:
                print("База данных пуста. Отчет не сформирован.")
                return
            
            df.to_excel(output_filename, index=False)
            print(f"Отчет успешно сформирован и сохранен как: {output_filename}")
            
    except Exception as e:
        print(f"Ошибка при экспорте в Excel: {e}")

def change_status(complaint_id, new_status, admin_name="admin"):
    """Меняет статус обращения и записывает действие в историю."""
    try:
        with closing(sqlite3.connect(DB_NAME, timeout=10)) as conn:
            with conn:  # Менеджер контекста автоматически делает commit при успехе
                cursor = conn.cursor()
                
                # Проверяем наличие заявки
                cursor.execute("SELECT status FROM complaints WHERE id = ?", (complaint_id,))
                row = cursor.fetchone()
                
                if not row:
                    print(f"Ошибка: Обращение с ID {complaint_id} не найдено в базе данных.")
                    return
                
                old_status = row[0]
                if old_status == new_status:
                    print(f"Статус обращения {complaint_id} уже равен '{new_status}'. Изменений не требуется.")
                    return

                now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                
                # Обновляем статус и дату последнего изменения
                cursor.execute(
                    "UPDATE complaints SET status = ?, updated_at = ? WHERE id = ?", 
                    (new_status, now, complaint_id)
                )
                
                # Записываем действие в лог истории
                cursor.execute(
                    """INSERT INTO complaint_history 
                       (complaint_id, changed_by, action, old_value, new_value, changed_at) 
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (complaint_id, admin_name, 'status_change', old_status, new_status, now)
                )
                
                print(f"Успех: Статус заявки №{complaint_id} изменен с '{old_status}' на '{new_status}'.")
                
    except Exception as e:
        print(f"Ошибка при изменении статуса в БД: {e}")

# ========================================================
# БЛОК ЗАПУСКА
# ========================================================
if __name__ == '__main__':
    # Выгрузка отчета (раскомментируй для использования)
    export_to_excel()
    
    # Пример смены статуса (раскомментируй и введи нужные данные)
    # change_status(complaint_id=1, new_status="in_progress")
    # change_status(complaint_id=1, new_status="closed")