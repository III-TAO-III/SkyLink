import PyInstaller.__main__
import customtkinter
import os

ctk_path = os.path.dirname(customtkinter.__file__)

args = [
    'gui.py',                        # Входной файл
    '--name=SkyLinkV0.83',                # Имя EXE
    '--onedir',                     # Один файл
    '--noconsole',                   # Без черного окна
    '--clean',                       # Очистка кэша
    f'--add-data={ctk_path};customtkinter', # Темы CTk
    '--add-data=events.json;.',      # Правила событий (config.get_resource_path)
    '--add-data=icon.ico;.',         # Иконка окна и трея (gui.resource_path)
    '--add-data=assets/fonts/Play-Regular.ttf;assets/fonts',  # Шрифт PLAY (gui)
    '--icon=icon.ico',               # Иконка EXE
]

print("🚀 Starting Build...")
PyInstaller.__main__.run(args)
print("✅ Done! Check 'dist' folder.")