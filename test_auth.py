import requests

# Настройки
URL = "http://localhost:3000/api/telemetry/skylink/verify"
API_KEY = "skb_77849cc8ebbeeac52ff82fccb6b2b942"  # <-- Сюда свой ключ

try:
    print(f"📡 Стучимся на {URL}...")
    response = requests.get(
        URL, 
        headers={"x-api-key": API_KEY},
        timeout=5
    )
    
    if response.status_code == 200:
        data = response.json()
        print("\n✅ УСПЕХ! Сервер ответил:")
        print(f"Валидность: {data.get('valid')}")
        print(f"Пилот: {data.get('commander')}")
    else:
        print(f"\n❌ ОШИБКА {response.status_code}:")
        print(response.text)

except Exception as e:
    print(f"\n💀 Сбой соединения: {e}")