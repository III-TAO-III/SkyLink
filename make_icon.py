from PIL import Image

def create_ico(source_file, output_file):
    try:
        img = Image.open(source_file)
        
        # Если картинка не квадратная, предупредим, но продолжим
        if img.size[0] != img.size[1]:
            print(f"⚠️ Внимание: Изображение {img.size} не квадратное. Иконка может быть сплюснута.")

        # Windows любит, когда в одном файле зашиты разные размеры
        icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        
        img.save(output_file, format='ICO', sizes=icon_sizes)
        print(f"✅ Успешно! Файл '{output_file}' создан.")
        
    except FileNotFoundError:
        print(f"❌ Ошибка: Файл '{source_file}' не найден. Положи его в папку проекта.")

if __name__ == "__main__":
    # Убедись, что твой файл называется logo.png
    create_ico("logo.png", "icon.ico")