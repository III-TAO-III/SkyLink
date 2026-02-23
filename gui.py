import ctypes
import json
import logging
import math
import os
import sys
import threading
import webbrowser
import winreg
from tkinter import BooleanVar

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw
from tendo import singleton

# Импорты логики
from config import CURRENT_SESSION, UI_STATE, Config
from main import start_background_service, stop_background_service
from sender import FAILED_ACCOUNTS, Sender
from updater import UpdateManager
from utils import verify_api_key

# --- Theme Setup ---
COLOR_BG = "#0a0a0f"
COLOR_BORDER = "#2a2a2f"
COLOR_ACCENT = "#f97316"  # Оранжевый
COLOR_GREEN = "#22c55e"
COLOR_RED = "#ef4444"
COLOR_TEXT_WHITE = "#ffffff"
COLOR_TEXT_GRAY = "#9ca3af"

# Цвета для кнопки портала (База и Яркий для пульсации)
PORTAL_BASE_RGB = (60, 20, 20)  # Темно-бордовый
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
        if hwnd == 0:
            hwnd = window_id  # Fallback

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


# --- Автозагрузка Windows (тот же ключ, что в setup.iss: SkyLinkAgent) ---
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_VALUE_NAME = "SkyLinkAgent"


def _get_startup_command():
    """Команда для записи в Run: exe или python + gui.py (в кавычках при пробелах)."""
    if getattr(sys, "frozen", False):
        path = sys.executable
        return f'"{path}"' if " " in path else path
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def is_app_in_startup():
    """Проверяет, есть ли приложение в автозагрузке (реестр HKCU Run)."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, REG_VALUE_NAME)
            return True
        except OSError:
            return False
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def add_app_to_startup():
    """Добавляет приложение в автозагрузку Windows."""
    cmd = _get_startup_command()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.SetValueEx(key, REG_VALUE_NAME, 0, winreg.REG_SZ, cmd)
        finally:
            winreg.CloseKey(key)
        return True
    except OSError as e:
        logging.warning("Could not add to startup: %s", e)
        return False


def remove_app_from_startup():
    """Удаляет приложение из автозагрузки Windows."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, REG_VALUE_NAME)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
        return True
    except OSError as e:
        logging.warning("Could not remove from startup: %s", e)
        return False


class AccountRow(ctk.CTkFrame):
    """Компонент строки аккаунта."""

    def __init__(self, master, name, api_key, app, is_new=False):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.commander_name = name
        self.is_new = is_new

        self.grid_columnconfigure(1, weight=1)

        # Name
        self.lbl_name = ctk.CTkLabel(
            self, text=name, font=("PLAY", 14), text_color=COLOR_TEXT_WHITE, anchor="w"
        )
        self.lbl_name.grid(row=0, column=0, padx=10, pady=(6, 2), sticky="w")

        # 2. Разделитель
        # FIX: corner_radius=0 обязателен, иначе линия исчезает.
        # height=2 делает её четче.
        separator = ctk.CTkFrame(self, height=2, fg_color="#333333", corner_radius=0)
        separator.grid(row=1, column=0, columnspan=2, sticky="ew", padx=30, pady=(5, 0))

        # Status/Actions Container
        self.status_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.status_frame.grid(row=0, column=1, padx=5, sticky="e")

        self.lbl_status = ctk.CTkLabel(
            self.status_frame, text="LINKED ✓", text_color=COLOR_GREEN, font=("PLAY", 11, "bold")
        )
        self.lbl_status.pack(side="left", padx=10)

        self.btn_change = ctk.CTkButton(
            self.status_frame,
            text="CHANGE API",
            width=90,
            height=24,
            fg_color="transparent",
            border_width=1,
            border_color="#333333",
            text_color="#9ca3af",
            hover_color="#18181b",
            command=self.show_edit_mode,
        )
        self.btn_change.pack(side="left", padx=5)

        self.btn_delete = ctk.CTkButton(
            self.status_frame,
            text="✕",
            width=30,
            height=30,
            fg_color="transparent",
            text_color="#555555",
            font=("PLAY", 16, "bold"),
            hover_color="#ef4444",
            command=self.show_confirm_delete,
        )
        self.btn_delete.pack(side="left")

        # Confirmation Mode Container
        self.confirm_frame = ctk.CTkFrame(self, fg_color="transparent")

        lbl_confirm = ctk.CTkLabel(self.confirm_frame, text="Delete?", text_color=COLOR_TEXT_GRAY)
        lbl_confirm.pack(side="left", padx=10)

        btn_yes = ctk.CTkButton(
            self.confirm_frame,
            text="YES",
            width=60,
            fg_color=COLOR_RED,
            hover_color="#B91C1C",
            command=self.confirm_delete,
        )
        btn_yes.pack(side="left", padx=5)

        btn_no = ctk.CTkButton(
            self.confirm_frame,
            text="NO",
            width=60,
            fg_color="#333333",
            hover_color="#444444",
            command=self.show_view_mode,
        )
        btn_no.pack(side="left")

        # Edit Mode Container
        self.edit_frame = ctk.CTkFrame(self, fg_color="transparent")

        self.entry_key = ctk.CTkEntry(
            self.edit_frame, placeholder_text="Paste NEW API Key...", width=300, height=28
        )
        self.entry_key.pack(side="left", padx=5)

        self.btn_paste = ctk.CTkButton(
            self.edit_frame,
            text="PASTE",
            width=50,
            height=28,
            fg_color="#333333",
            hover_color="#444444",
            command=self.paste_from_clipboard,
        )
        self.btn_paste.pack(side="left", padx=(0, 5))

        self.btn_save = ctk.CTkButton(
            self.edit_frame,
            text="SAVE",
            width=60,
            height=28,
            fg_color=COLOR_ACCENT,
            hover_color="#c2410c",
            command=self.save_key,
        )
        self.btn_save.pack(side="left", padx=5)

        self.btn_cancel = ctk.CTkButton(
            self.edit_frame,
            text="✕",
            width=30,
            height=28,
            fg_color="#333333",
            hover_color="#444444",
            command=self.cancel_edit,
        )
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
        cache_file = self.app.config.app_data_dir / "deduplication_cache.json"
        self.app.config.delete_account(self.commander_name)
        Sender.purge_commander_cache(self.commander_name, cache_file)
        self.destroy()

    def save_key(self):
        if not self.winfo_exists():
            return
        new_key = self.entry_key.get().strip()
        if not new_key:
            return

        try:
            self.btn_save.configure(text="...", state="disabled")
            self.app.update()
        except:
            return

        # 1. Проверяем ключ на сервере
        is_valid, result_name = verify_api_key(new_key, self.app.config.API_URL)

        if is_valid:
            # 2. УСПЕХ!
            # Мы не сравниваем имена. Мы просто берем то имя, которое вернул сервер (result_name),
            # и сохраняем аккаунт под этим именем.
            self.app.config.save_account(result_name, new_key)
            FAILED_ACCOUNTS.discard(result_name)  # сразу сбрасываем INVALID, чтобы показать LINKED
            self.app.refresh_account_list()  # Обновляем список, чтобы увидеть нового пилота
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
    """Получает абсолютный путь к ресурсу, работает и для dev, и для PyInstaller"""
    try:
        # PyInstaller создает временную папку и хранит путь в _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def load_font_windows(font_path):
    """Программная регистрация шрифта в системе на время работы сессии."""
    if not os.path.exists(font_path):
        return False
    # FR_PRIVATE = 0x10 (шрифт виден только текущему процессу)
    # FR_NOT_ENUM = 0x20 (шрифт не отображается в списке других программ)
    path_buf = ctypes.create_unicode_buffer(font_path)
    if ctypes.windll.gdi32.AddFontResourceExW(path_buf, 0x10, None):
        return True
    return False


# Загрузка шрифта PLAY до создания окна (путь учитывает PyInstaller)
_play_font_path = resource_path(os.path.join("assets", "fonts", "Play-Regular.ttf"))
if not load_font_windows(_play_font_path):
    logging.warning("Play font not loaded from %s, UI may use fallback font.", _play_font_path)


class SkyLinkGUI(ctk.CTk):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.running = True
        self.tray_icon = None
        self.last_tray_color = None
        self.pulse_phase = 0.0
        self._current_view = None
        self._service_started = False

        # 1. Настройка окна
        self.overrideredirect(True)
        self.geometry("640x420")
        self.configure(fg_color=COLOR_BORDER)
        self.title("SkyLink Agent")
        self.center_window()

        # 1.1. Иконка
        myappid = "skybioml.skylink.agent.1.0"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

        try:
            icon_path = resource_path("icon.ico")
            self.iconbitmap(icon_path)
        except Exception as e:
            print(f"Icon load error: {e}")  # В консоли увидим, если что не так

        self.running = True

        # 2. EVENT BINDING: Это правильный способ починить Taskbar.
        # Мы говорим: "Как только окно появится (<Map>), примени фикс".
        self.bind("<Map>", self.on_window_map)

        # 3. Структура интерфейса (Сэндвич)
        self.inner_frame = ctk.CTkFrame(self, fg_color=COLOR_BG, corner_radius=0)
        self.inner_frame.pack(expand=True, fill="both", padx=1, pady=(1, 4))

        self.create_header()
        self.create_footer()
        self.create_body()

        if self.config.last_accepted_version != self.config.SOFTWARE_VERSION:
            self._draw_disclaimer_view()
        else:
            self._draw_main_view()

        self.start_tray_icon()
        self.update_ui_loop()
        self.updater = UpdateManager(self.config)
        self._update_info = None
        self._update_info_clicked = None
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

        # 5. Start minimized only when MAIN view; otherwise show window so user can act (Disclaimer/Update)
        if self._load_start_minimized_setting() and self._current_view == "MAIN":
            self.withdraw()
        else:
            self.show_window()

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
        self.geometry(f"640x420+{int(x)}+{int(y)}")

    def create_header(self):
        self.header = ctk.CTkFrame(self.inner_frame, fg_color="#18181b", height=44, corner_radius=0)
        self.header.pack(side="top", fill="x")

        # Drag Logic
        self.header.bind("<Button-1>", self.start_move)
        self.header.bind("<B1-Motion>", self.do_move)

        ctk.CTkLabel(self.header, text="⚡", text_color=COLOR_ACCENT, font=("PLAY", 16)).pack(
            side="left", padx=(15, 5)
        )
        ctk.CTkLabel(
            self.header, text="SKYLINK AGENT", font=("PLAY", 12, "bold"), text_color="white"
        ).pack(side="left")

        ctk.CTkButton(
            self.header,
            text="✕",
            width=30,
            height=30,
            fg_color="transparent",
            hover_color=COLOR_RED,
            command=self.minimize_to_tray,
        ).pack(side="right", padx=5)

        # Pulsing Portal Button
        self.btn_portal = ctk.CTkButton(
            self.header,
            text="SkyBioML Portal",
            width=120,
            height=28,
            fg_color="#2a0a0a",  # Начальный цвет
            hover_color="#551a1a",
            border_width=1,
            border_color="#5a2020",
            text_color="#ffcccc",
            command=lambda: webbrowser.open(
                self.config.API_URL.replace("/api/telemetry/skylink", "")
            ),
        )
        self.btn_portal.pack(side="right", padx=10)

    def create_footer(self):
        self.footer = ctk.CTkFrame(self.inner_frame, fg_color="transparent", height=40)
        self.footer.pack(side="bottom", fill="x", padx=15, pady=10)

        self.btn_add = ctk.CTkButton(
            self.footer,
            text="+ ADD ACCOUNT",  # КАПСОМ стильнее
            font=("PLAY", 11, "bold"),
            fg_color="transparent",  # Прозрачная
            border_width=1,
            border_color="#3f3f46",
            text_color="#9ca3af",
            hover_color="#27272a",  # Subtle hover
            width=120,
            height=32,  # Чуть выше
            command=self.add_manual_account,
        )
        self.btn_add.pack(side="left")

        self._start_minimized_var = BooleanVar(value=self._load_start_minimized_setting())
        self.chk_start_minimized = ctk.CTkCheckBox(
            self.footer,
            text="Start minimized",
            variable=self._start_minimized_var,
            font=("PLAY", 11),
            text_color=COLOR_TEXT_GRAY,
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=self._on_start_minimized_changed,
        )
        self.chk_start_minimized.pack(side="left", padx=(15, 0))

        self._run_at_startup_var = BooleanVar(value=is_app_in_startup())
        self.chk_run_at_startup = ctk.CTkCheckBox(
            self.footer,
            text="RUN AT STARTUP",
            variable=self._run_at_startup_var,
            font=("PLAY", 11),
            text_color=COLOR_TEXT_GRAY,
            fg_color="#3f3f46",
            hover_color="#52525b",
            command=self._on_run_at_startup_changed,
        )
        self.chk_run_at_startup.pack(side="left", padx=(15, 0))

        self.btn_update = ctk.CTkButton(
            self.footer,
            text="",
            font=("PLAY", 11),
            fg_color=COLOR_GREEN,
            hover_color="#16a34a",
            width=140,
            height=28,
            command=self._on_update_click,
        )
        self.btn_update.pack(side="right", padx=(5, 0))
        self.btn_update.pack_forget()  # hidden by default (optional show when update in MAIN view)

        self.lbl_footer_status = ctk.CTkLabel(
            self.footer, text="Initializing...", text_color=COLOR_TEXT_GRAY, font=("PLAY", 11)
        )
        self.lbl_footer_status.pack(side="right")

    def _settings_path(self):
        return self.config.app_data_dir / "settings.json"

    def _load_start_minimized_setting(self):
        try:
            p = self._settings_path()
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f).get("start_minimized", False)
        except Exception:
            pass
        return False

    def _save_start_minimized_setting(self, value):
        self.config.set_setting("start_minimized", bool(value))

    def _on_start_minimized_changed(self):
        self._save_start_minimized_setting(self._start_minimized_var.get())

    def _on_run_at_startup_changed(self):
        want = self._run_at_startup_var.get()
        if want:
            if not add_app_to_startup():
                self._run_at_startup_var.set(False)
        else:
            if not remove_app_from_startup():
                self._run_at_startup_var.set(True)

    def _check_for_updates_worker(self):
        """Runs in daemon thread. On update found (and frozen EXE), switch to update view on main thread."""
        try:
            result = self.updater.check_for_updates()
            if result and getattr(sys, "frozen", False):
                self.after(0, lambda r=result: (self._clear_body(), self._draw_update_view(r)))
        except Exception as e:
            logging.debug("Update check error: %s", e)

    def _show_update_button(self, update_info):
        """Called on main thread. Show update button with version."""
        self._update_info = update_info
        self.btn_update.configure(text=f"⬇ Install {update_info['version']}")
        self.btn_update.pack(side="right", padx=(5, 0))

    def _on_update_click(self):
        """Disable install button (footer or update-view), start download in background thread."""
        self._update_info_clicked = self._update_info or getattr(
            self, "_update_info_clicked", None
        )
        self._update_info = None
        if not self._update_info_clicked:
            return
        if self._current_view == "UPDATE" and getattr(self, "_update_install_btn", None) and self._update_install_btn.winfo_exists():
            self._update_install_btn.configure(state="disabled", text="Downloading...")
        elif getattr(self, "btn_update", None) and self.btn_update.winfo_exists():
            self.btn_update.configure(state="disabled", text="Downloading...")
        threading.Thread(target=self._download_update_worker, daemon=True).start()

    def _download_update_worker(self):
        """Runs in background thread. On success schedule run_installer_and_exit on main thread."""
        try:
            url = self.updater.find_installer_url(self._update_info_clicked.get("assets") or [])
            if not url:
                self.after(0, self._on_update_download_failed)
                return
            path = self.updater.download_installer(url)
            self.after(0, lambda: self.updater.run_installer_and_exit(path))
        except Exception as e:
            logging.warning("Update download failed: %s", e)
            self.after(0, self._on_update_download_failed)

    def _on_update_download_failed(self):
        """Reset install button on main thread after failed download."""
        if not self.winfo_exists():
            return
        self.show_window()
        info = getattr(self, "_update_info_clicked", None)
        version = info.get("version", "?") if info else "?"
        if self._current_view == "UPDATE" and getattr(self, "_update_install_btn", None) and self._update_install_btn.winfo_exists():
            self._update_install_btn.configure(state="normal", text=f"INSTALL {version}")
        elif getattr(self, "btn_update", None) and self.btn_update.winfo_exists():
            self.btn_update.configure(state="normal", text=f"⬇ Install {version}")

    def _clear_body(self):
        """Destroy all children of body_frame (keeps body_frame itself)."""
        for widget in self.body_frame.winfo_children():
            widget.destroy()

    def create_body(self):
        """Create only the empty body_frame. Content is drawn by _draw_*_view()."""
        self.body_frame = ctk.CTkFrame(self.inner_frame, fg_color="transparent")
        self.body_frame.pack(side="top", fill="both", expand=True, padx=5, pady=5)

    def _draw_main_view(self):
        """Draw main app view: Active Commander, Registered Accounts, scroll list. Start background service if needed."""
        self._current_view = "MAIN"
        self._clear_body()

        # 1. Active Commander Block
        self.active_frame = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        self.active_frame.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(
            self.active_frame,
            text="ACTIVE COMMANDER:",
            text_color=COLOR_TEXT_GRAY,
            font=("PLAY", 10, "bold"),
        ).pack(anchor="w")

        pilot_row = ctk.CTkFrame(self.active_frame, fg_color="transparent")
        pilot_row.pack(fill="x", pady=(0, 5))
        pilot_row.grid_columnconfigure(1, weight=1)

        self.lbl_commander = ctk.CTkLabel(
            pilot_row, text="WAITING...", font=("PLAY", 20, "bold"), text_color=COLOR_TEXT_WHITE
        )
        self.lbl_commander.grid(row=0, column=0, sticky="w")

        main_width = 640
        status_win_width = int(main_width * 0.4 * 33 / 22)
        status_win_height = 28
        self._marquee_visible_chars = 33
        self._marquee_offset = 0
        self._marquee_tick = 0
        self._marquee_full_text = ""
        self.status_win = ctk.CTkFrame(
            pilot_row, width=status_win_width, height=status_win_height, fg_color="transparent"
        )
        self.status_win.grid(row=0, column=2, padx=(0, 30), sticky="e")
        self.status_win.grid_propagate(False)
        self.lbl_full_status = ctk.CTkLabel(
            self.status_win,
            text="Initializing...",
            text_color=COLOR_TEXT_GRAY,
            font=("PLAY", 20, "bold"),
            anchor="e",
        )
        self.lbl_full_status.place(relx=1, rely=0.5, anchor="e", x=-8)

        # 2. Section Header with Line
        header_row = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        header_row.pack(fill="x", padx=10, pady=(15, 5))

        ctk.CTkLabel(
            header_row,
            text="REGISTERED ACCOUNTS",
            text_color=COLOR_TEXT_GRAY,
            font=("PLAY", 10, "bold"),
        ).pack(side="left")

        line = ctk.CTkFrame(header_row, height=2, fg_color="#333333", corner_radius=0)
        line.pack(side="left", fill="x", expand=True, padx=(15, 0), pady=(5, 0))

        # 3. Scroll List
        self.scroll_frame = ctk.CTkScrollableFrame(
            self.body_frame,
            fg_color="transparent",
            scrollbar_button_color="#1a1a1a",
            scrollbar_button_hover_color="#333333",
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=0, pady=5)

        self.refresh_account_list()
        if not self._service_started:
            threading.Thread(target=start_background_service, args=(self.config,), daemon=True).start()
            self._service_started = True

    def _draw_disclaimer_view(self):
        """Draw disclaimer view: title, EN/RU text, Lang toggle, Accept and Exit. On Accept -> save version, then main view."""
        self._current_view = "DISCLAIMER"
        self._clear_body()

        def current_content():
            return self._DISCLAIMER_RU if self.config.language == "ru" else self._DISCLAIMER_EN

        top_frame = ctk.CTkFrame(self.body_frame, fg_color=COLOR_BG, corner_radius=0)
        top_frame.pack(side="top", fill="x", padx=1, pady=(1, 0))
        title_label = ctk.CTkLabel(
            top_frame, text=current_content()["title"], font=("PLAY", 12, "bold"), text_color=COLOR_TEXT_WHITE
        )
        title_label.pack(side="left", padx=15, pady=10)

        textbox = ctk.CTkTextbox(
            self.body_frame, wrap="word", state="disabled", font=("PLAY", 11), fg_color="#0a0a0f"
        )
        textbox.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        def apply_content():
            c = current_content()
            title_label.configure(text=c["title"])
            textbox.configure(state="normal")
            textbox.delete("1.0", "end")
            textbox.insert("1.0", c["body"])
            textbox.configure(state="disabled")
            btn_accept.configure(text=c["accept"])
            btn_exit.configure(text=c["decline"])
            lang_btn.configure(text="RU" if self.config.language == "en" else "EN")

        def toggle_lang():
            self.config.language = "ru" if self.config.language == "en" else "en"
            apply_content()

        lang_btn = ctk.CTkButton(
            top_frame, text="RU" if self.config.language == "en" else "EN",
            width=50, height=28, fg_color="#3f3f46", command=toggle_lang
        )
        lang_btn.pack(side="right", padx=15, pady=8)

        bot_frame = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        bot_frame.pack(side="bottom", fill="x", padx=1, pady=(0, 10))
        btn_frame = ctk.CTkFrame(bot_frame, fg_color="transparent")
        btn_frame.pack(pady=10, padx=15)

        def on_accept():
            self.config.disclaimer_accepted = True
            self.config.set_setting("accepted_version", self.config.SOFTWARE_VERSION)
            self.config.last_accepted_version = self.config.SOFTWARE_VERSION
            self.config.save_disclaimer_state()
            self._clear_body()
            self._draw_main_view()

        def on_exit():
            self.quit_app()

        btn_accept = ctk.CTkButton(
            btn_frame, text=current_content()["accept"], fg_color=COLOR_GREEN, hover_color="#16a34a", command=on_accept
        )
        btn_accept.pack(side="left", padx=(0, 10))
        btn_exit = ctk.CTkButton(
            btn_frame, text=current_content()["decline"], fg_color=COLOR_RED, hover_color="#dc2626", command=on_exit
        )
        btn_exit.pack(side="left")
        apply_content()
        self.show_window()

    def _draw_update_view(self, update_info):
        """Draw mandatory update view: CRITICAL UPDATE title, body text, INSTALL button. No skip."""
        self._current_view = "UPDATE"
        self._update_info = update_info
        self._clear_body()

        title = ctk.CTkLabel(
            self.body_frame,
            text="CRITICAL UPDATE",
            font=("PLAY", 14, "bold"),
            text_color=COLOR_ACCENT,
        )
        title.pack(side="top", padx=15, pady=(15, 5))

        textbox = ctk.CTkTextbox(
            self.body_frame, wrap="word", state="disabled", font=("PLAY", 11), fg_color="#0a0a0f"
        )
        textbox.pack(side="top", fill="both", expand=True, padx=10, pady=5)
        textbox.configure(state="normal")
        textbox.insert("1.0", update_info.get("body", "") or "")
        textbox.configure(state="disabled")

        btn_frame = ctk.CTkFrame(self.body_frame, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", pady=15, padx=15)
        self._update_install_btn = ctk.CTkButton(
            btn_frame,
            text=f"INSTALL {update_info.get('version', '')}",
            font=("PLAY", 12, "bold"),
            fg_color=COLOR_GREEN,
            hover_color="#16a34a",
            height=40,
            command=self._on_update_click,
        )
        self._update_install_btn.pack(pady=5)
        self.show_window()

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
            pystray.MenuItem("Exit", self.quit_app),
        )
        self.tray_icon = pystray.Icon(
            "SkyLink", self.create_tray_image("gray"), "SkyLink Agent", menu
        )
        self.tray_icon.run()

    def create_tray_image(self, color):
        width, height = 64, 64
        image = Image.new("RGB", (width, height), (30, 30, 30))
        dc = ImageDraw.Draw(image)
        fill_map = {
            "red": (239, 68, 68),
            "green": (34, 197, 94),
            "yellow": (255, 193, 7),
            "gray": (100, 100, 100),
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

    # --- First-run disclaimer modal (EN/RU) ---
    _DISCLAIMER_EN = {
        "title": "SkyLink Privacy & Data Policy",
        "body": """ATTENTION: Data Transmission Policy

This application automatically synchronizes your game data with two systems:

1. EDDN (Elite Dangerous Data Network) - Global Public Network:
   - Sent: Star coordinates, planet scan data, signals, FSD jumps.
   - Purpose: Updating public databases (Inara, Spansh, EDSM).
   - Privacy: Commander name is anonymized/hashed by the network protocol. Personal data is NOT sent.

2. SkyBioML Portal - Private Squadron Server:
   - Sent: Ship status, loadouts, cargo, location, credits.
   - Purpose: Squadron management tools and analytics.
   - Privacy: Data is accessible only to authorized squadron members.

By using SkyLink, you explicitly consent to the automated transmission of navigation and exploration data to these networks.""",
        "accept": "I Accept & Continue",
        "decline": "Decline & Exit",
    }
    _DISCLAIMER_RU = {
        "title": "Политика конфиденциальности SkyLink",
        "body": """ВНИМАНИЕ: Политика передачи данных

Это приложение автоматически синхронизирует ваши данные с двумя системами:

1. Глобальная сеть EDDN (Elite Dangerous Data Network):
   - Отправляются: Координаты звезд, данные сканирования планет, сигналы.
   - Цель: Обновление общедоступных баз (Inara, Spansh, EDSM).
   - Приватность: Имя пилота анонимизируется протоколом. Личные данные НЕ отправляются.

2. Портал SkyBioML (Приватный сервер):
   - Отправляются: Статус корабля, фиты, груз, местоположение.
   - Цель: Работа инструментов эскадрильи.
   - Приватность: Данные доступны только авторизованным членам эскадрильи.

Используя SkyLink, вы подтверждаете согласие на автоматическую отправку навигационных и исследовательских данных.""",
        "accept": "Принимаю и Продолжить",
        "decline": "Отказаться и Выйти",
    }

    # --- Animation & Loop ---
    def update_ui_loop(self):
        # Если флаг выключения поднят или окна уже нет — выходим сразу
        if not self.running or not self.winfo_exists():
            return
        if self._current_view != "MAIN":
            self.after(50, self.update_ui_loop)
            return

        try:
            # 0. Автооткрытие окна из трея при 401/403 (как по клику по иконке в трее)
            if UI_STATE.pop("request_show_window", False):
                self.show_window()

            # 1. Получаем текущее состояние
            status_text = UI_STATE.get("status", "Idle")
            current_cmdr = CURRENT_SESSION.get("commander")
            api_key = CURRENT_SESSION.get("api_key")
            # Когда командир определён по логу, но ключа нет — показываем явное требование ключа
            if current_cmdr and not api_key:
                status_text = "API KEY is required!!!"
            st_lower = status_text.lower()

            # 2. Обновляем блок активного пилота: полный статус в окне (бегущая строка, если не помещается)
            if status_text != self._marquee_full_text:
                self._marquee_full_text = status_text
                self._marquee_offset = 0
                self._marquee_tick = 0
            n = self._marquee_visible_chars
            if len(status_text) > n:
                loop_text = (status_text + "   ") * 2
                start = self._marquee_offset % len(loop_text)
                display = (loop_text[start:] + loop_text[:start])[:n]
                self._marquee_tick += 1
                if self._marquee_tick % 3 == 0:
                    self._marquee_offset += 1
                self.lbl_full_status.configure(text=display)
            else:
                self.lbl_full_status.configure(text=status_text)
            if current_cmdr:
                self.lbl_commander.configure(text=current_cmdr)
            else:
                self.lbl_commander.configure(text="WAITING FOR SIGNAL...")

            # 3. Краткий статус внизу (в футере)
            if current_cmdr:
                if current_cmdr and not api_key:
                    self.lbl_footer_status.configure(text="NO KEY ●", text_color=COLOR_RED)
                elif "waiting" in st_lower or "standby" in st_lower or "closed" in st_lower:
                    self.lbl_footer_status.configure(text="STANDBY ●", text_color="#FFC107")
                elif (
                    "error" in st_lower
                    or "failed" in st_lower
                    or "invalid" in st_lower
                    or "network" in st_lower
                ):
                    self.lbl_footer_status.configure(text="ERROR ●", text_color=COLOR_RED)
                else:
                    self.lbl_footer_status.configure(text="CONNECTED ●", text_color=COLOR_GREEN)
            else:
                self.lbl_footer_status.configure(text="", text_color=COLOR_TEXT_GRAY)

            # 4. Обновляем статусы авторизации в списке
            # (Тут свой try/except не нужен, общий поймает)
            for widget in self.scroll_frame.winfo_children():
                if isinstance(widget, AccountRow):
                    widget.update_auth_status()

            # 5. Логика цвета Трея
            target_color = "gray"
            if current_cmdr and not api_key:
                target_color = "red"
            elif "waiting" in st_lower or "standby" in st_lower or "closed" in st_lower:
                target_color = "yellow"
            elif "running" in st_lower or "sent" in st_lower or "monitoring" in st_lower:
                target_color = "green"
            elif "error" in st_lower or "failed" in st_lower or "invalid" in st_lower:
                target_color = "red"

            if self.tray_icon and target_color != self.last_tray_color:
                self.tray_icon.icon = self.create_tray_image(target_color)
                self.last_tray_color = target_color

            # 6. Пульсация (Вот здесь раньше падало)
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
        if not self.winfo_exists():
            return
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        accounts = self.config.accounts
        if not accounts:
            ctk.CTkLabel(self.scroll_frame, text="No accounts linked yet.", text_color="gray").pack(
                pady=10
            )
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
        sys.exit()  # Если копия есть — сразу выходим, ничего не запуская

    # 2. Если мы одни — начинаем работу
    config = Config()
    app = SkyLinkGUI(config)
    app.protocol("WM_DELETE_WINDOW", app.minimize_to_tray)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.quit_app()
