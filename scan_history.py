import json
import glob
import os
import sys
from pathlib import Path
from config import Config  # Используем твой умный конфиг для поиска пути

def scan_history():
    # 1. Инициализация
    print("⏳ Инициализация конфигурации...")
    try:
        conf = Config()
        journal_dir = conf.journal_path
    except Exception as e:
        print(f"❌ Ошибка загрузки конфига: {e}")
        return

    if not journal_dir or not os.path.exists(journal_dir):
        print("❌ Не удалось найти папку с журналами Elite Dangerous.")
        return

    # 2. Поиск файлов
    pattern = os.path.join(journal_dir, "Journal.*.log")
    log_files = sorted(glob.glob(pattern))
    
    total_files = len(log_files)
    print(f"📂 Найдено лог-файлов: {total_files}")
    print(f"📂 Папка: {journal_dir}")
    print("-" * 40)

    # Словарь для хранения результата: "EventName": { набор ключей }
    schema_map = {}
    
    # Счётчик событий
    event_counts = {}

    # 3. Сканирование
    for i, log_file in enumerate(log_files, 1):
        filename = os.path.basename(log_file)
        # Красивый вывод прогресса (перезапись строки)
        sys.stdout.write(f"\r🚀 Сканирование файла [{i}/{total_files}]: {filename}")
        sys.stdout.flush()

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        event_type = entry.get("event")
                        
                        if not event_type:
                            continue

                        # Если событие встретилось впервые — создаем заготовку
                        if event_type not in schema_map:
                            schema_map[event_type] = {
                                "action": "send",         # По умолчанию
                                "deduplicate": False      # По умолчанию
                            }
                            event_counts[event_type] = 0

                        # Считаем
                        event_counts[event_type] += 1

                        # Мержим поля (добавляем новые найденные ключи)
                        for key in entry.keys():
                            # Игнорируем сам ключ 'event', так как он и так в названии блока
                            if key != "event":
                                schema_map[event_type][key] = True

                    except json.JSONDecodeError:
                        continue # Битая строка, бывает
        except Exception as e:
            print(f"\n⚠ Ошибка чтения файла {filename}: {e}")

    print(f"\n\n✅ Сканирование завершено!")
    print("-" * 40)

    # 4. Сортировка результата (по алфавиту)
    sorted_schema = dict(sorted(schema_map.items()))

    # 5. Сохранение в файл
    output_filename = "unified_schema_dump.json"
    
    with open(output_filename, 'w', encoding='utf-8') as f:
        # Пишем кастомный JSON дампер, чтобы поля были в одну строку (компактно), 
        # а события — блоками. Стандартный indent=2 растянет файл на 10км.
        
        f.write("{\n")
        keys = list(sorted_schema.keys())
        for idx, event_name in enumerate(keys):
            data = sorted_schema[event_name]
            
            # Формируем красивую строку
            # Сначала action и deduplicate
            action_part = f'"action": "{data.pop("action")}", "deduplicate": {str(data.pop("deduplicate")).lower()}'
            
            # Потом остальные поля
            fields_part = ", ".join([f'"{k}": {str(v).lower()}' for k, v in data.items()])
            
            # Собираем блок
            block = f'  "{event_name}": {{ {action_part}, {fields_part} }}'
            
            # Запятая в конце, если это не последний элемент
            comma = "," if idx < len(keys) - 1 else ""
            f.write(block + comma + "\n")
        
        f.write("}\n")

    print(f"💾 Результат сохранен в файл: {output_filename}")
    print(f"📊 Всего уникальных типов событий: {len(sorted_schema)}")

if __name__ == "__main__":
    scan_history()