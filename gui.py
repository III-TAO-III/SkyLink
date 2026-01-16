import customtkinter as ctk
import threading
import os
import sys
import webbrowser
import logging
import pystray
import ctypes
import math
import time
from PIL import Image, ImageDraw
from tendo import singleton
import sys

# Импорты логики
from config import Config, CURRENT_SESSION, UI_STATE
from utils import verify_api_key
from main import start_background_service, stop_background_service
from sender import Sender, FAILED_ACCOUNTS

# --- Theme Setup ---
COLOR_BG = "#0a0a0f"
COLOR_BORDER = "#2a2a2f"
COLOR_ACCENT = "#f97316"     # Оранжевый
COLOR_GREEN = "#22c55e"
COLOR_RED = "#ef4444"
COLOR_TEXT_WHITE = "#ffffff"
COLOR_TEXT_GRAY = "#9ca3af"

# Цвета для кнопки портала (База и Яркий для пульсации)
PORTAL_BASE_RGB = (60, 20, 20)   # Темно-бордовый
PORTAL_GLOW_RGB = (220, 40, 40)  # Ярко-красный

ctk.set_appearance_mode("Dark")

# --- Helper: Color Interpolation (для пульсации) ---
def lerp_color(color1, color2, t):
    """Смешивает два цвета (RGB tuple) на основе t (0.0 to 1.0). Возвращает Hex."""
    r = int(color1[0] + (color2[0] - color1[0]) * t)
    g = int(color1[1] + (color2[1] - color1[1]) * t)
    b = int(color1[2] + (color2[2] - color1[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"

# --- Windows API Logic (Correct implementation) ---
def apply_taskbar_fix(window_id):
    """
    Применяет стили WS_EX_APPWINDOW к окну, чтобы оно было видно в панели задач.
    Вызывается строго по событию <Map>.
    """
    try:
        hwnd = ctypes.windll.user32.GetParent(window_id)
        if hwnd == 0: hwnd = window_id # Fallback

        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        
        # Если стиль еще не применен
        if not (style & WS_EX_APPWINDOW):
            style = style & ~WS_EX_TOOLWINDOW
            style = style | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            # Форсируем перерисовку панели задач
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027) 
    except Exception as e:
        print(f"WinAPI Error: {e}")

class AccountRow(ctk.CTkFrame):
    """Компонент строки аккаунта."""
    def __init__(self, master, name, api_key, app, is_new=False):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.commander_name = name
        self.is_new = is_new
        
        self.grid_columnconfigure(1, weight=1)

        # Name
        self.lbl_name = ctk.CTkLabel(self, text=name, font=("Roboto Medium", 14), text_color=COLOR_TEXT_WHITE, anchor="w")
        self.lbl_name.grid(row=0, column=0, padx=10, pady=(6, 2), sticky="w")
        
        # 2. Разделитель
        # FIX: corner_radius=0 обязателен, иначе линия исчезает.
        # height=2 делает её четче.
        separator = ctk.CTkFrame(self, height=2, fg_color="#333333", corner_radius=0)
        separator.grid(row=1, column=0, columnspan=2, sticky="ew", padx=30, pady=(5, 0))

        # Status/Actions Container
        self.status_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.status_frame.grid(row=0, column=1, padx=5, sticky="e")
        
        self.lbl_status = ctk.CTkLabel(self.status_frame, text="LINKED ✓", text_color=COLOR_GREEN, font=("Consolas", 11, "bold"))
        self.lbl_status.pack(side="left", padx=10)

        self.btn_change = ctk.CTkButton(self.status_frame, text="CHANGE API", width=90, height=24, fg_color="transparent", border_width=1, border_color="#333333", text_color="#9ca3af", hover_color="#18181b", command=self.show_edit_mode)
        self.btn_change.pack(side="left", padx=5)

        self.btn_delete = ctk.CTkButton(
            self.status_frame, 
            text="✕", 
            width=30, 
            height=30, 
            fg_color="transparent", 
            text_color="#555555",
            font=("Arial", 16, "bold"),
            hover_color="#ef4444", 
            command=self.show_confirm_delete
        )
        self.btn_delete.pack(side="left")
        
        # Confirmation Mode Container
        self.confirm_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        lbl_confirm = ctk.CTkLabel(self.confirm_frame, text="Delete?", text_color=COLOR_TEXT_GRAY)
        lbl_confirm.pack(side="left", padx=10)
        
        btn_yes = ctk.CTkButton(self.confirm_frame, text="YES", width=60, fg_color=COLOR_RED, hover_color="#B91C1C", command=self.confirm_delete)
        btn_yes.pack(side="left", padx=5)
        
        btn_no = ctk.CTkButton(self.confirm_frame, text="NO", width=60, fg_color="#333333", hover_color="#444444", command=self.show_view_mode)
        btn_no.pack(side="left")

        # Edit Mode Container
        self.edit_frame = ctk.CTkFrame(self, fg_color="transparent")
        
        self.entry_key = ctk.CTkEntry(self.edit_frame, placeholder_text="Paste NEW API Key...", width=300, height=28)
        self.entry_key.pack(side="left", padx=5)
        
        self.btn_paste = ctk.CTkButton(self.edit_frame, text="PASTE", width=50, height=28, fg_color="#333333", hover_color="#444444", command=self.paste_from_clipboard)
        self.btn_paste.pack(side="left", padx=(0, 5))

        self.btn_save = ctk.CTkButton(self.edit_frame, text="SAVE", width=60, height=28, fg_color=COLOR_ACCENT, hover_color="#c2410c", command=self.save_key)
        self.btn_save.pack(side="left", padx=5)

        self.btn_cancel = ctk.CTkButton(self.edit_frame, text="✕", width=30, height=28, fg_color="#333333", hover_color="#444444", command=self.cancel_edit)
        self.btn_cancel.pack(side="left")

    def cancel_edit(self):
        if self.is_new:
            self.destroy()
        else:
            self.show_view_mode()

    def paste_from_clipboard(self):
        try:
            self.entry_key.delete(0, "end")
            self.entry_key.insert(0, self.clipboard_get())
        except:
            pass

    def show_edit_mode(self):
        self.status_frame.grid_forget()
        self.edit_frame.grid(row=0, column=1, padx=5, sticky="e")

    def show_view_mode(self):
        self.edit_frame.grid_forget()
        self.confirm_frame.grid_forget()
        self.status_frame.grid(row=0, column=1, padx=5, sticky="e")

    def show_confirm_delete(self):
        self.status_frame.grid_forget()
        self.edit_frame.grid_forget()
        self.confirm_frame.grid(row=0, column=1, padx=5, sticky="e")

    def confirm_delete(self):
        cache_file = self.app.config.app_data_dir / 'deduplication_cache.json'
        self.app.config.delete_account(self.commander_name)
        Sender.purge_commander_cache(self.commander_name, cache_file)
        self.destroy()

    def save_key(self):
        if not self.winfo_exists(): return
        new_key = self.entry_key.get().strip()
        if not new_key: return

        try:
            self.btn_save.configure(text="...", state="disabled")
            self.app.update()
        except: return

        # 1. Проверяем ключ на сервере
        is_valid, result_name = verify_api_key(new_key, self.app.config.API_URL)

        if is_valid:
            # 2. УСПЕХ!
            # Мы не сравниваем имена. Мы просто берем то имя, которое вернул сервер (result_name),
            # и сохраняем аккаунт под этим именем.
            self.app.config.save_account(result_name, new_key)
            self.app.refresh_account_list() # Обновляем список, чтобы увидеть нового пилота
        else:
            # 3. ОШИБКА
            self.entry_key.delete(0, "end")
            self.entry_key.configure(placeholder_text="Invalid Key")
            self.btn_save.configure(text="SAVE", state="normal")

    def update_auth_status(self):
        """Проверяет, не забанен ли этот пилот сервером."""
        if self.commander_name in FAILED_ACCOUNTS:
            # Если в черном списке — показываем красный крест
            self.lbl_status.configure(text="INVALID ✕", text_color=COLOR_RED)
        else:
            # Иначе — все ок (мы не проверяем "LINKED" каждую секунду, считаем что ок)
            self.lbl_status.configure(text="LINKED ✓", text_color=COLOR_GREEN)

def resource_path(relative_path):
    """ Получает абсолютный путь к ресурсу, работает и для dev, и для PyInstaller """
    try:
        # PyInstaller создает временную папку и хранит путь в _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

class SkyLinkGUI(ctk.CTk):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.running = True
        self.tray_icon = None
        self.last_tray_color = None
        self.pulse_phase = 0.0

        # 1. Настройка окна
        self.overrideredirect(True)
        self.geometry("640x420")
        self.configure(fg_color=COLOR_BORDER)
        self.title("SkyLink Agent")
        self.center_window()

        # 1.1. Иконка
        myappid = 'skybioml.skylink.agent.1.0' 
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        
        try:
            icon_path = resource_path("icon.ico")
            self.iconbitmap(icon_path)
        except Exception as e:
            print(f"Icon load error: {e}") # В консоли увидим, если что не так

        self.running = True

        # 2. EVENT BINDING: Это правильный способ починить Taskbar.
        # Мы говорим: "Как только окно появится (<Map>), примени фикс".
        self.bind("<Map>", self.on_window_map)

        # 3. Структура интерфейса (Сэндвич)
        self.inner_frame = ctk.CTkFrame(self, fg_color=COLOR_BG, corner_radius=0)
        self.inner_frame.pack(expand=True, fill="both", padx=1, pady=(1, 4))
        
        self.create_header()
        self.create_footer() # Footer второй!
        self.create_body()   # Body третий!
        
        # 4. Запуск процессов
        self.start_tray_icon()
        self.update_ui_loop()
        self.refresh_account_list()

    def on_window_map(self, event):
        """Событие срабатывает, когда окно реально отрисовано ОС."""
        # Проверяем, что событие пришло от самого окна, а не от виджета внутри
        if event.widget == self:
            apply_taskbar_fix(self.winfo_id())
            # Отвязываем, чтобы не спамить (достаточно одного раза при старте)
            self.unbind("<Map>")

    def center_window(self):
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width / 2) - (640 / 2)
        y = (screen_height / 2) - (420 / 2)
        self.geometry(f'640x420+{int(x)}+{int(y)}')

    def create_header(self):
        self.header = ctk.CTkFrame(self.inner_frame, fg_color="#18181b", height=44, corner_radius=0)
        self.header.pack(side="top", fill="x")
        
        # Drag Logic
        self.header.bind("<Button-1>", self.start_move)
        self.header.bind("<B1-Motion>", self.do_move)
        
        ctk.CTkLabel(self.header, text="⚡", text_color=COLOR_ACCENT, font=("Arial", 16)).pack(side="left", padx=(15, 5))
        ctk.CTkLabel(self.header, text="SKYLINK AGENT", font=("Arial", 12, "bold"), text_color="white").pack(side="left")
        
        ctk.CTkButton(self.header, text="✕", width=30, height=30, fg_color="transparent", hover_color=COLOR_RED, command=self.minimize_to_tray).pack(side="right", padx=5)
        
        # Pulsing Portal Button
        self.btn_portal = ctk.CTkButton(
            self.header, 
            text="SkyBioML Portal", 
            width=120, height=28,
            fg_color="#2a0a0a",            # Начальный цвет
            hover_color="#551a1a",
            border_width=1,
            border_color="#5a2020",
            text_color="#ffcccc",
            command=lambda: webbrowser.open(self.config.API_URL.replace("/api/telemetry/skylink", ""))
        )
        self.btn_portal.pack(side="right", padx=10)

    def create_footer(self):
        self.footer = ctk.CTkFrame(self.inner_frame, fg_color="transparent", height=40)
        self.footer.pack(side="bottom", fill="x", padx=15, pady=10)
        
        self.btn_add = ctk.CTkButton(
            self.footer, 
            text="+ ADD ACCOUNT",          # КАПСОМ стильнее
            font=("Arial", 11, "bold"),
            fg_color="transparent",        # Прозрачная
            border_width=1, 
            border_color="#3f3f46", 
            text_color="#9ca3af", 
            hover_color="#27272a",         # Subtle hover
            width=120, 
            height=32,                     # Чуть выше
            command=self.add_manual_account
        )
        self.btn_add.pack(side="left")
        
        self.lbl_footer_status = ctk.CTkLabel(self.footer, text="Initializing...", text_color=COLOR_TEXT_GRAY, font=("Arial", 11))
        self.lbl_footer_status.pack(side="right")

    def create_body(self):
        self.body_frame = ctk.CTkFrame(self.inner_frame, fg_color="transparent")
        self.body_frame.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        # 1. Active Commander Block
        self.active_frame = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        self.active_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        ctk.CTkLabel(self.active_frame, text="ACTIVE COMMANDER:", text_color=COLOR_TEXT_GRAY, font=("Arial", 10, "bold")).pack(anchor="w")
        
        pilot_row = ctk.CTkFrame(self.active_frame, fg_color="transparent")
        pilot_row.pack(fill="x", pady=(0, 5))
        
        self.lbl_commander = ctk.CTkLabel(pilot_row, text="WAITING...", font=("Arial", 20, "bold"), text_color=COLOR_TEXT_WHITE)
        self.lbl_commander.pack(side="left")
        self.lbl_active_status = ctk.CTkLabel(pilot_row, text="", font=("Arial", 12, "bold"), text_color=COLOR_GREEN)
        self.lbl_active_status.pack(side="right", padx=(0, 30))

        # 2. Section Header with Line (Решение твоей проблемы)
        # Создаем контейнер для заголовка
        header_row = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        header_row.pack(fill="x", padx=10, pady=(15, 5)) # pady=15 дает отступ сверху, отделяя от Active Commander
        
        # Текст
        lbl = ctk.CTkLabel(header_row, text="REGISTERED ACCOUNTS", text_color=COLOR_TEXT_GRAY, font=("Arial", 10, "bold"))
        lbl.pack(side="left")
        
        # Линия справа от текста
        line = ctk.CTkFrame(header_row, height=2, fg_color="#333333", corner_radius=0)
        line.pack(side="left", fill="x", expand=True, padx=(15, 0), pady=(5,0)) # pady выравнивает линию по центру текста

        # 3. Scroll List
        self.scroll_frame = ctk.CTkScrollableFrame(
            self.body_frame, 
            fg_color="transparent",
            scrollbar_button_color="#1a1a1a",
            scrollbar_button_hover_color="#333333"
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=0, pady=5)

    # --- Window Moving ---
    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        x = self.winfo_x() + event.x - self.x
        y = self.winfo_y() + event.y - self.y
        self.geometry(f"+{x}+{y}")

    # --- Tray Logic ---
    def start_tray_icon(self):
        threading.Thread(target=self.setup_tray, daemon=True).start()

    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Open SkyLink", self.show_window, default=True),
            pystray.MenuItem("Exit", self.quit_app)
        )
        self.tray_icon = pystray.Icon("SkyLink", self.create_tray_image("gray"), "SkyLink Agent", menu)
        self.tray_icon.run()

    def create_tray_image(self, color):
        width, height = 64, 64
        image = Image.new('RGB', (width, height), (30, 30, 30))
        dc = ImageDraw.Draw(image)
        fill_map = {
            "red": (239, 68, 68),
            "green": (34, 197, 94),
            "yellow": (255, 193, 7),
            "gray": (100, 100, 100)
        }
        fill = fill_map.get(color, (100, 100, 100))
        dc.ellipse((16, 16, 48, 48), fill=fill)
        return image

    def minimize_to_tray(self):
        self.withdraw()

    def show_window(self, icon=None, item=None):
        self.deiconify()
        self.lift()
        self.focus_force()
        # При восстановлении снова применяем фикс (на всякий случай, некоторые версии Windows сбрасывают стили)
        apply_taskbar_fix(self.winfo_id())

    def quit_app(self, icon=None, item=None):
        """Чистый выход без Traceback."""
        self.running = False
        if self.tray_icon: 
            self.tray_icon.stop()
        
        # Останавливаем фоновый сервис
        stop_background_service()
        
        self.destroy()
        # Принудительный выход, чтобы убить все daemon-потоки
        sys.exit(0) 

    # --- Animation & Loop ---
    def update_ui_loop(self):
        # Если флаг выключения поднят или окна уже нет — выходим сразу
        if not self.running or not self.winfo_exists(): 
            return
        
        try:
            # 1. Получаем текущее состояние
            status_text = UI_STATE.get("status", "Idle")
            st_lower = status_text.lower()
            current_cmdr = CURRENT_SESSION.get("commander")

            # 2. Обновляем блок активного пилота
            if current_cmdr:
                self.lbl_commander.configure(text=current_cmdr)
                
                # Мягкая проверка статуса
                if "waiting" in st_lower or "standby" in st_lower or "closed" in st_lower:
                    self.lbl_active_status.configure(text="STANDBY ●", text_color="#FFC107")
                elif "error" in st_lower or "failed" in st_lower or "invalid" in st_lower or "network" in st_lower:
                    self.lbl_active_status.configure(text="ERROR ●", text_color=COLOR_RED)
                else:
                    self.lbl_active_status.configure(text="CONNECTED ●", text_color=COLOR_GREEN)
            else:
                self.lbl_commander.configure(text="WAITING FOR SIGNAL...")
                self.lbl_active_status.configure(text="", text_color="gray")

            # 3. Обновляем статусы авторизации в списке
            # (Тут свой try/except не нужен, общий поймает)
            for widget in self.scroll_frame.winfo_children():
                if isinstance(widget, AccountRow):
                    widget.update_auth_status()
            
            # 4. Логика цвета Трея
            target_color = "gray"
            if "waiting" in st_lower or "standby" in st_lower or "closed" in st_lower:
                target_color = "yellow"
            elif "running" in st_lower or "sent" in st_lower or "monitoring" in st_lower:
                target_color = "green"
            elif "error" in st_lower or "failed" in st_lower or "invalid" in st_lower:
                target_color = "red"
                
            self.lbl_footer_status.configure(text=f"STATUS: {status_text}")
            
            if self.tray_icon and target_color != self.last_tray_color:
                self.tray_icon.icon = self.create_tray_image(target_color)
                self.last_tray_color = target_color

            # 5. Пульсация (Вот здесь раньше падало)
            self.pulse_phase += 0.15
            t = (math.sin(self.pulse_phase) + 1) / 2
            new_text_color = lerp_color((255, 200, 200), (255, 100, 100), t)
            new_border_color = lerp_color((90, 32, 32), (220, 40, 40), t)
            
            self.btn_portal.configure(text_color=new_text_color, border_color=new_border_color)

            # Запускаем снова через 50 мс
            self.after(50, self.update_ui_loop)

        except (KeyboardInterrupt, RuntimeError, Exception):
            # Если возникла ошибка (например, окно уничтожено во время анимации)
            # Мы просто выходим из цикла. Это норма при закрытии.
            pass

    def refresh_account_list(self):
        if not self.winfo_exists(): return
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
        
        accounts = self.config.accounts
        if not accounts:
            ctk.CTkLabel(self.scroll_frame, text="No accounts linked yet.", text_color="gray").pack(pady=10)
        else:
            for name, key in accounts.items():
                AccountRow(self.scroll_frame, name, key, self).pack(fill="x", pady=1)

    def add_manual_account(self):
        row = AccountRow(self.scroll_frame, "NEW CMDR", "", self, is_new=True)
        row.pack(fill="x", pady=2)
        row.show_edit_mode()

if __name__ == "__main__":
    # 1. Сначала проверяем, не запущены ли мы уже
    try:
        me = singleton.SingleInstance() 
    except singleton.SingleInstanceException:
        sys.exit() # Если копия есть — сразу выходим, ничего не запуская

    # 2. Если мы одни — начинаем работу
    config = Config()
    
    # Передаем наш config внутрь функции start_background_service
    bg_thread = threading.Thread(target=start_background_service, args=(config,), daemon=True)
    bg_thread.start()

    app = SkyLinkGUI(config)
    app.protocol("WM_DELETE_WINDOW", app.minimize_to_tray)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.quit_app()