import customtkinter as ctk
import threading
import sys
import os
import webbrowser
import logging
import pystray
from PIL import Image, ImageDraw
import mouse  # Для перетаскивания окна без рамки
from config import Config, CURRENT_SESSION, UI_STATE
from utils import verify_api_key

# --- Настройка темы (цвета из WidgetFrame.tsx) ---
COLOR_BG = "#0a0a0f"
COLOR_BORDER = "#2a2a2f"  # border-white/10 imitation
COLOR_ACCENT = "#f97316"  # Orange
COLOR_GREEN = "#22c55e"
COLOR_RED = "#ef4444"
COLOR_TEXT_WHITE = "#ffffff"
COLOR_TEXT_GRAY = "#9ca3af"

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

class AccountRow(ctk.CTkFrame):
    """Строка одного аккаунта: отображает статус или форму ввода."""
    def __init__(self, master, name, api_key, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.commander_name = name
        self.current_key = api_key
        
        # Grid layout
        self.grid_columnconfigure(1, weight=1)

        # 1. Имя пилота
        self.lbl_name = ctk.CTkLabel(
            self, text=name, 
            font=("Roboto Medium", 14), 
            text_color=COLOR_TEXT_WHITE,
            anchor="w"
        )
        self.lbl_name.grid(row=0, column=0, padx=10, pady=5, sticky="w")

        # 2. Контейнер для статуса/кнопок
        self.status_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.status_frame.grid(row=0, column=1, padx=5, sticky="e")

        # 2a. Статус (по умолчанию)
        self.lbl_status = ctk.CTkLabel(
            self.status_frame, 
            text="API KEY LINKED ✓", 
            text_color=COLOR_GREEN, 
            font=("Arial", 11, "bold")
        )
        self.lbl_status.pack(side="left", padx=10)

        # 2b. Кнопка "Change"
        self.btn_change = ctk.CTkButton(
            self.status_frame, 
            text="CHANGE API", 
            width=80, 
            height=24,
            fg_color="#27272a", 
            hover_color=COLOR_ACCENT,
            command=self.show_edit_mode
        )
        self.btn_change.pack(side="left")

        # 3. Контейнер для редактирования (скрыт по умолчанию)
        self.edit_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        self.entry_key = ctk.CTkEntry(
            self.edit_frame, 
            placeholder_text="Paste API Key here...", 
            width=200, 
            height=28,
            show="*" # Скрываем символы
        )
        self.entry_key.pack(side="left", padx=5)
        if api_key: self.entry_key.insert(0, api_key)

        self.btn_save = ctk.CTkButton(
            self.edit_frame, 
            text="VERIFY & SAVE", 
            width=100, 
            height=28,
            fg_color=COLOR_ACCENT,
            hover_color="#c2410c",
            command=self.save_key
        )
        self.btn_save.pack(side="left", padx=5)

    def show_edit_mode(self):
        self.status_frame.grid_forget()
        self.edit_frame.grid(row=0, column=1, padx=5, sticky="e")

    def show_view_mode(self):
        self.edit_frame.grid_forget()
        self.status_frame.grid(row=0, column=1, padx=5, sticky="e")

    def save_key(self):
        new_key = self.entry_key.get().strip()
        if not new_key: return

        # Проверка ключа через сервер
        self.btn_save.configure(text="CHECKING...", state="disabled")
        self.app.update() # Force UI refresh

        is_valid, result_name = verify_api_key(new_key, self.app.config.API_URL)

        if is_valid:
            # Если имя совпадает или это новый аккаунт
            if result_name == self.commander_name:
                self.app.config.save_account(self.commander_name, new_key)
                self.current_key = new_key
                self.lbl_status.configure(text="LINKED ✓", text_color=COLOR_GREEN)
                self.show_view_mode()
                self.app.refresh_ui_state()
            else:
                self.entry_key.delete(0, "end")
                self.entry_key.configure(placeholder_text=f"Error: Key belongs to {result_name}!")
        else:
             self.entry_key.delete(0, "end")
             self.entry_key.configure(placeholder_text="Invalid API Key")
        
        self.btn_save.configure(text="VERIFY & SAVE", state="normal")


class SkyLinkApp(ctk.CTk):
    def __init__(self, config_instance):
        super().__init__()
        self.config = config_instance
        self.running = True

        # --- Настройка окна (Безрамочное, кастомное) ---
        self.overrideredirect(True) # Убираем стандартную рамку Windows
        self.geometry("450x300")
        self.configure(fg_color=COLOR_BORDER) # Цвет границы
        
        # Центрирование
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width/2) - (450/2)
        y = (screen_height/2) - (300/2)
        self.geometry('%dx%d+%d+%d' % (450, 300, x, y))

        # --- Внутренний контейнер (Основной фон) ---
        # Имитация border-b-[4px] за счет паддинга снизу
        self.inner_frame = ctk.CTkFrame(self, fg_color=COLOR_BG, corner_radius=0)
        self.inner_frame.pack(expand=True, fill="both", padx=1, pady=(1, 4))

        # --- 1. HEADER ---
        self.header = ctk.CTkFrame(self.inner_frame, fg_color="#18181b", height=40, corner_radius=0)
        self.header.pack(fill="x")
        
        # Drag Logic (Перетаскивание за заголовок)
        self.header.bind("<Button-1>", self.start_move)
        self.header.bind("<B1-Motion>", self.do_move)

        # Лого + Название
        self.lbl_icon = ctk.CTkLabel(self.header, text="⚡", text_color=COLOR_ACCENT, font=("Arial", 16))
        self.lbl_icon.pack(side="left", padx=(15, 5))
        
        self.lbl_title = ctk.CTkLabel(self.header, text="SKYLINK AGENT", font=("Arial", 12, "bold"), text_color="white")
        self.lbl_title.pack(side="left")

        # Кнопки справа
        self.btn_close = ctk.CTkButton(
            self.header, text="✕", width=30, height=30, 
            fg_color="transparent", hover_color="#ef4444", 
            command=self.minimize_to_tray
        )
        self.btn_close.pack(side="right", padx=5)

        # Кнопка портала
        self.btn_portal = ctk.CTkButton(
            self.header, text="🌐", width=30, height=30,
            fg_color="transparent", hover_color="#3b82f6",
            command=lambda: webbrowser.open(self.config.API_URL.replace("/api/telemetry/skylink", ""))
        )
        self.btn_portal.pack(side="right")

        # --- 2. ACTIVE COMMANDER ---
        self.active_frame = ctk.CTkFrame(self.inner_frame, fg_color="transparent")
        self.active_frame.pack(fill="x", padx=15, pady=10)
        
        self.lbl_active_title = ctk.CTkLabel(self.active_frame, text="ACTIVE COMMANDER:", text_color=COLOR_TEXT_GRAY, font=("Arial", 10))
        self.lbl_active_title.pack(anchor="w")

        self.lbl_commander = ctk.CTkLabel(
            self.active_frame, 
            text="WAITING FOR SIGNAL...", 
            font=("Arial", 20, "bold"), 
            text_color=COLOR_TEXT_WHITE
        )
        self.lbl_commander.pack(anchor="w", pady=(0, 5))

        # --- 3. ACCOUNTS LIST ---
        self.lbl_list_title = ctk.CTkLabel(self.inner_frame, text="REGISTERED ACCOUNTS:", text_color=COLOR_TEXT_GRAY, font=("Arial", 10))
        self.lbl_list_title.pack(anchor="w", padx=15)

        self.scroll_frame = ctk.CTkScrollableFrame(self.inner_frame, fg_color="transparent", height=120)
        self.scroll_frame.pack(fill="both", expand=True, padx=5)

        # --- 4. FOOTER ---
        self.footer = ctk.CTkFrame(self.inner_frame, fg_color="transparent", height=30)
        self.footer.pack(fill="x", side="bottom", padx=15, pady=10)

        self.btn_add = ctk.CTkButton(
            self.footer, 
            text="+ Add Account", 
            fg_color="#27272a", 
            hover_color=COLOR_ACCENT,
            width=100,
            command=self.add_manual_account
        )
        self.btn_add.pack(side="left")

        self.lbl_status = ctk.CTkLabel(self.footer, text="Initializing...", text_color=COLOR_TEXT_GRAY, font=("Arial", 11))
        self.lbl_status.pack(side="right")

        # --- System Tray Setup ---
        self.tray_icon = None
        self.tray_thread = threading.Thread(target=self.setup_tray, daemon=True)
        self.tray_thread.start()

        # Start Update Loop
        self.update_ui_loop()
        self.refresh_account_list()

    # --- Перетаскивание окна ---
    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.winfo_x() + deltax
        y = self.winfo_y() + deltay
        self.geometry(f"+{x}+{y}")

    # --- Логика Трея ---
    def create_tray_image(self, color):
        # Рисуем кружок для иконки
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), (255, 255, 255))
        dc = ImageDraw.Draw(image)
        dc.rectangle((0, 0, width, height), fill=(30, 30, 30)) # Темный фон
        
        # Цветная точка
        if color == "red": fill = (239, 68, 68)
        elif color == "green": fill = (34, 197, 94)
        else: fill = (100, 100, 100)
        
        dc.ellipse((16, 16, 48, 48), fill=fill)
        return image

    def setup_tray(self):
        icon_image = self.create_tray_image("gray")
        menu = pystray.Menu(
            pystray.MenuItem("Open SkyLink", self.show_window),
            pystray.MenuItem("Exit", self.quit_app)
        )
        self.tray_icon = pystray.Icon("SkyLink", icon_image, "SkyLink Agent", menu)
        self.tray_icon.run()

    def minimize_to_tray(self):
        self.withdraw() # Скрыть окно

    def show_window(self, icon=None, item=None):
        self.deiconify() # Показать окно
        self.lift()
        self.focus_force()

    def quit_app(self, icon, item):
        self.tray_icon.stop()
        self.running = False
        self.destroy()
        sys.exit()

    # --- Логика Интерфейса ---
    def update_ui_loop(self):
        if not self.running: return

        # 1. Update Active Commander Text
        current_cmdr = CURRENT_SESSION.get("commander")
        if current_cmdr:
            self.lbl_commander.configure(text=f"🚀 {current_cmdr}")
        else:
            self.lbl_commander.configure(text="WAITING FOR SIGNAL...")

        # 2. Update Status & Colors
        status_text = UI_STATE.get("status", "Idle")
        status_color = UI_STATE.get("color", "gray")
        
        self.lbl_status.configure(text=f"STATUS: {status_text}")
        
        # Обновляем иконку в трее (если изменился цвет)
        if self.tray_icon:
            self.tray_icon.icon = self.create_tray_image(status_color)

        self.after(1000, self.update_ui_loop)

    def refresh_account_list(self):
        # Очистить список
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        # Загрузить из конфига
        accounts = self.config.accounts
        if not accounts:
            lbl = ctk.CTkLabel(self.scroll_frame, text="No accounts linked yet.", text_color="gray")
            lbl.pack(pady=10)
        
        for name, key in accounts.items():
            row = AccountRow(self.scroll_frame, name, key, self)
            row.pack(fill="x", pady=2)

    def refresh_ui_state(self):
        """Вызывается когда что-то сохранилось, чтобы обновить список"""
        self.refresh_account_list()

    def add_manual_account(self):
        # Добавляет пустую строку для ввода
        row = AccountRow(self.scroll_frame, "New Commander", "", self)
        row.pack(fill="x", pady=2)
        row.show_edit_mode() # Сразу открываем поле ввода

# --- Запуск ---
def run_gui():
    conf = Config()
    app = SkyLinkApp(conf)
    
    # Запускаем основной процесс в фоне (импорт внутри функции чтобы избежать цикла)
    from main import start_background_service
    bg_thread = threading.Thread(target=start_background_service, daemon=True)
    bg_thread.start()

    app.mainloop()

if __name__ == "__main__":
    run_gui()