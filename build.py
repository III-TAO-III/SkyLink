import PyInstaller.__main__
import customtkinter
import os

ctk_path = os.path.dirname(customtkinter.__file__)

args = [
    'gui.py',                        # Входной файл
    '--name=SkyLink',                # Имя EXE
    '--onefile',                     # Один файл
    '--noconsole',                   # Без черного окна
    '--clean',                       # Очистка кэша
    f'--add-data={ctk_path};customtkinter', # Темы CTk
    '--add-data=events.json;.',      # <--- ВАЖНО: Зашиваем правила внутрь
    # '--icon=icon.ico',             # Раскомментируй, если нашел иконку
]

print("🚀 Starting Build...")
PyInstaller.__main__.run(args)
print("✅ Done! Check 'dist' folder.")