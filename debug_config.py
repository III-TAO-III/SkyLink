import json
import os
try:
    from config import Config
except ImportError:
    print("❌ Ошибка импорта: Не удалось импортировать класс 'Config' из config.py")
    exit()

def test_rules():
    print("--- 🕵️ АУДИТ КОНФИГУРАЦИИ ---")
    
    # 1. Инициализируем конфиг и загружаем правила
    try:
        config = Config()
        rules = config.event_rules
        print(f"✅ Правила загружены. Всего типов событий: {len(rules)}")
    except Exception as e:
        print(f"❌ Ошибка при инициализации конфига: {e}")
        return

    # 2. Тестовые кейсы (проверяем разные категории)
    check_list = [
        "Materials",      # Должен быть: send + deduplicate
        "Music",          # Должен быть: ignore
        "FSDJump",        # Должен быть: send
        "NonExistent",    # Должен быть: None или default
        "Loadout"         # Должен быть: send + deduplicate
    ]

    print("\n--- 🔍 ПРОВЕРКА КЛЮЧЕВЫХ СОБЫТИЙ ---")
    header = f"{'СОБЫТИЕ':<15} | {'ДЕЙСТВИЕ':<10} | {'ДЕДУПЛИКАЦИЯ'}"
    print(header)
    print("-" * len(header))

    for event in check_list:
        rule = rules.get(event)
        
        if rule:
            action = rule.get('action', 'N/A')
            dedup = str(rule.get('deduplicate', False))
            print(f"{event:<15} | {action:<10} | {dedup}")
        else:
            print(f"{event:<15} | {'MISSING':<10} | -")

if __name__ == "__main__":
    test_rules()