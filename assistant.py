import speech_recognition as sr
import pyttsx3
import os
import sys
import tkinter as tk
import threading
import pvporcupine
import pyaudio
import struct
import time
import datetime
import webbrowser
import subprocess
import shutil
import re
import json

# Optional tray support
TRAY_AVAILABLE = False
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

# =======================
# PATH / INSTALL FIX
# =======================
def get_resource_path(filename):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.abspath("."), filename)

KEYWORD_PATH = get_resource_path("kyra.ppn")

def get_app_folder():
    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(appdata, "KyraAssistant")
    os.makedirs(folder, exist_ok=True)
    return folder

APP_FOLDER = get_app_folder()
CONFIG_FILE = os.path.join(APP_FOLDER, "config.json")
STABLE_EXE = os.path.join(APP_FOLDER, "KyraAssistant.exe")

RUNNING_AS_EXE = getattr(sys, "frozen", False)
APP_EXECUTABLE = os.path.abspath(sys.executable) if RUNNING_AS_EXE else os.path.abspath(__file__)

def ensure_installed_executable():
    """
    If user runs the downloaded EXE from Downloads/Desktop,
    copy it into AppData and relaunch from a stable permanent path.
    """
    global APP_EXECUTABLE

    if not RUNNING_AS_EXE:
        return

    current_exe = os.path.abspath(sys.executable)

    if os.path.normcase(current_exe) == os.path.normcase(STABLE_EXE):
        APP_EXECUTABLE = current_exe
        return

    try:
        shutil.copy2(current_exe, STABLE_EXE)
        APP_EXECUTABLE = STABLE_EXE
        subprocess.Popen([STABLE_EXE], cwd=APP_FOLDER)
        sys.exit(0)
    except Exception as e:
        print("Install copy error:", e)
        APP_EXECUTABLE = current_exe

ensure_installed_executable()

# =======================
# ACCESS KEY
# =======================
def get_access_key():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = (data.get("porcupine_access_key") or "").strip()
            if key:
                return key
        except Exception:
            pass

    result = {"key": None}

    popup = tk.Tk()
    popup.title("Kyra Setup")
    popup.geometry("340x180")
    popup.configure(bg="#0a0f1c")
    popup.attributes("-topmost", True)
    popup.resizable(False, False)

    tk.Label(
        popup,
        text="Enter Porcupine Access Key",
        bg="#0a0f1c",
        fg="white",
        font=("Arial", 11, "bold")
    ).pack(pady=12)

    entry = tk.Entry(popup, width=38)
    entry.pack(pady=4)

    error_label = tk.Label(popup, text="", bg="#0a0f1c", fg="red")
    error_label.pack(pady=4)

    def save_key():
        key = entry.get().strip()
        if not key:
            error_label.config(text="Please enter a valid key")
            return
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"porcupine_access_key": key}, f)
            result["key"] = key
            popup.destroy()
        except Exception as e:
            error_label.config(text=f"Save failed: {e}")

    def on_close():
        popup.destroy()

    tk.Button(popup, text="Save", command=save_key, bg="#00cc88", fg="black").pack(pady=10)
    popup.protocol("WM_DELETE_WINDOW", on_close)
    popup.mainloop()

    if result["key"]:
        return result["key"]

    sys.exit(0)

ACCESS_KEY = get_access_key()

# =======================
# STARTUP (REGISTRY)
# =======================
def add_to_startup():
    try:
        if not RUNNING_AS_EXE:
            return

        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "KyraAssistant", 0, winreg.REG_SZ, APP_EXECUTABLE)
        winreg.CloseKey(key)
    except Exception as e:
        print("Startup add error:", e)

def remove_startup():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS,
        )
        winreg.DeleteValue(key, "KyraAssistant")
        winreg.CloseKey(key)
    except Exception:
        pass

add_to_startup()

# =======================
# STATE
# =======================
pending_action = None
file_data = {}
last_path = None
listening_active = False
last_activity_time = time.time()
AUTO_SLEEP_SECONDS = 60

tray_icon = None
engine_lock = threading.Lock()

# =======================
# UI
# =======================
root = tk.Tk()
root.title("KYRA AI")
root.geometry("340x230")
root.configure(bg="#0a0f1c")
root.attributes("-topmost", True)
root.overrideredirect(True)

frame = tk.Frame(root, bg="#0a0f1c")
frame.pack(fill="both", expand=True, padx=10, pady=10)

status_label = tk.Label(frame, text="● IDLE", fg="cyan", bg="#0a0f1c", font=("Arial", 10, "bold"))
status_label.pack(pady=5)

user_label = tk.Label(frame, text="", fg="white", bg="#0a0f1c", wraplength=300, justify="left")
user_label.pack(pady=6)

ai_label = tk.Label(frame, text="KYRA READY", fg="#00ffcc", bg="#0a0f1c", wraplength=300, justify="left")
ai_label.pack(pady=6)

button_row = tk.Frame(frame, bg="#0a0f1c")
button_row.pack(pady=10)

def hide_window():
    try:
        root.withdraw()
    except Exception:
        pass

def show_window():
    try:
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(200, lambda: root.attributes("-topmost", True))
    except Exception:
        pass

def terminate_process(delay=0.5):
    def do_exit():
        try:
            if tray_icon:
                tray_icon.stop()
        except Exception:
            pass
        time.sleep(delay)
        os._exit(0)

    threading.Thread(target=do_exit, daemon=True).start()

def disable_and_exit():
    """
    Permanent stop:
    - removes startup
    - deletes saved access key
    - exits
    """
    try:
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
    except Exception:
        pass

    try:
        remove_startup()
    except Exception:
        pass

    ui_set(ai_label, "Kyra disabled permanently")
    speak("Kyra disabled permanently")
    terminate_process(0.8)

def exit_current_session():
    """
    Temporary exit:
    - current session closes
    - startup and saved key remain
    """
    speak("Closing Kyra")
    terminate_process(0.5)

def ui_set(widget, text=None, fg=None):
    def update():
        try:
            if text is not None:
                widget.config(text=text)
            if fg is not None:
                widget.config(fg=fg)
        except Exception:
            pass
    try:
        root.after(0, update)
    except Exception:
        pass

tk.Button(button_row, text="Hide", command=hide_window, bg="#222", fg="white", width=10).grid(row=0, column=0, padx=6)
tk.Button(button_row, text="Exit", command=exit_current_session, bg="#444", fg="white", width=10).grid(row=0, column=1, padx=6)
tk.Button(button_row, text="STOP", command=disable_and_exit, bg="red", fg="white", width=10).grid(row=0, column=2, padx=6)

def drag(e):
    root.geometry(f"+{e.x_root}+{e.y_root}")

frame.bind("<B1-Motion>", drag)
root.protocol("WM_DELETE_WINDOW", hide_window)
root.withdraw()

# =======================
# SPEECH
# =======================
r = sr.Recognizer()
r.energy_threshold = 250
r.pause_threshold = 0.8

# =======================
# HELPERS
# =======================
def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None

def has_phrase(text: str, phrase: str) -> bool:
    return phrase in text

def google_search(q):
    q = q.strip()
    if not q:
        return "Nothing to search"
    webbrowser.open(f"https://www.google.com/search?q={q.replace(' ', '+')}")
    return f"Searching {q}"

def get_known_folder(folder_name: str) -> str:
    user = os.path.expanduser("~")
    candidates = [
        os.path.join(user, "OneDrive", folder_name),
        os.path.join(user, folder_name),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[-1]

def get_path(text):
    text = normalize_text(text)
    if "desktop" in text:
        return get_known_folder("Desktop")
    if "documents" in text:
        return get_known_folder("Documents")
    if "downloads" in text:
        return get_known_folder("Downloads")
    if "a drive" in text or text == "a":
        return "A:\\"
    if "c drive" in text or text == "c":
        return "C:\\"
    return os.getcwd()

def quick_open_direct(name: str):
    q = normalize_text(name)

    if "chrome" in q:
        os.system("start chrome")
        return "Opening Chrome"

    if "edge" in q or "msedge" in q:
        os.system("start msedge")
        return "Opening Edge"

    if "notepad" in q:
        os.system("notepad")
        return "Opening Notepad"

    if "calculator" in q or q == "calc":
        os.system("calc")
        return "Opening Calculator"

    if "paint" in q or "mspaint" in q:
        os.system("mspaint")
        return "Opening Paint"

    if "file explorer" in q or q == "explorer":
        os.system("explorer")
        return "Opening File Explorer"

    if "youtube" in q:
        webbrowser.open("https://youtube.com")
        return "Opening YouTube"

    if "vscode" in q or "vs code" in q or "visual studio code" in q:
        try:
            subprocess.Popen("code", shell=True)
            return "Opening VS Code"
        except Exception:
            pass

    return None

def open_uwp_app(query: str):
    try:
        output = subprocess.check_output(
            'powershell "Get-StartApps | Select-Object Name,AppID"',
            shell=True,
            stderr=subprocess.DEVNULL,
        ).decode(errors="ignore")

        q = normalize_text(query)

        for line in output.splitlines():
            line_low = normalize_text(line)
            if q in line_low:
                parts = line.split()
                if parts:
                    app_id = parts[-1]
                    subprocess.Popen(f'explorer shell:AppsFolder\\{app_id}', shell=True)
                    return f"Opening {query}"
    except Exception:
        pass
    return None

def open_application(name: str):
    query = normalize_text(name)

    direct = quick_open_direct(query)
    if direct:
        return direct

    # Special handling for UWP apps like WhatsApp
    if "whatsapp" in query:
        res = open_uwp_app("whatsapp")
        if res:
            return res

    # Search these locations for shortcuts / exe
    search_terms = [query]
    if "arduino" in query:
        search_terms += ["arduino", "arduino ide", "arduino-ide"]
    if "vscode" in query or "vs code" in query or "visual studio code" in query:
        search_terms += ["vscode", "vs code", "visual studio code", "code"]
    if "whatsapp" in query:
        search_terms += ["whatsapp"]

    folders = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        get_known_folder("Desktop"),
    ]

    for folder in folders:
        if not folder or not os.path.exists(folder):
            continue

        for root_dir, _, files in os.walk(folder):
            for file in files:
                low = file.lower()
                if low.endswith((".lnk", ".url", ".appref-ms", ".exe")):
                    if any(term in low for term in search_terms):
                        path = os.path.join(root_dir, file)
                        try:
                            os.startfile(path)
                            return f"Opening {file.replace('.lnk', '').replace('.url', '').replace('.exe', '')}"
                        except Exception:
                            pass

    # One more try for VS Code if command is available
    if "vscode" in query or "vs code" in query or "visual studio code" in query:
        try:
            subprocess.Popen("code", shell=True)
            return "Opening VS Code"
        except Exception:
            pass

    return None

def search_file(name: str):
    query = normalize_text(name)
    search_dirs = [
        get_known_folder("Desktop"),
        get_known_folder("Documents"),
        get_known_folder("Downloads"),
        os.getcwd(),
    ]

    for folder in search_dirs:
        if not os.path.exists(folder):
            continue

        for root_dir, _, files in os.walk(folder):
            for file in files:
                if query in normalize_text(file):
                    path = os.path.join(root_dir, file)
                    try:
                        os.startfile(path)
                    except Exception:
                        pass
                    return f"Found {file}"

    return "File not found"

def create_item():
    global last_path

    try:
        path = file_data["path"]
        name = file_data["name"].replace(" ", "_")
        item_type = file_data["type"]
        last_path = path

        if item_type == "file":
            if "." not in name:
                name += ".txt"
            full_path = os.path.join(path, name)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write("Created by Kyra")

        elif item_type == "folder":
            full_path = os.path.join(path, name)
            os.makedirs(full_path, exist_ok=True)

        if os.path.exists(full_path):
            try:
                os.startfile(full_path)
            except Exception:
                pass
            return f"{item_type} created successfully"

        return "Creation failed"

    except Exception as e:
        print("Create Error:", e)
        return "Error creating item"

def run_system(action: str):
    try:
        if action == "shutdown":
            os.system("shutdown /s /t 1")
        elif action == "restart":
            os.system("shutdown /r /t 1")
        elif action == "lock":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
        elif action == "sleep":
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        return f"{action} executed"
    except Exception as e:
        print("System action error:", e)
        return f"Could not perform {action}"

# =======================
# TTS
# =======================
def speak(text):
    def run():
        try:
            with engine_lock:
                engine = pyttsx3.init()
                engine.setProperty("rate", 175)
                engine.setProperty("volume", 1.0)
                engine.say(text)
                engine.runAndWait()
                engine.stop()
        except Exception as e:
            print("TTS Error:", e)

    ui_set(ai_label, f"🤖 {text}")
    threading.Thread(target=run, daemon=True).start()

# =======================
# TRAY
# =======================
def create_tray_image():
    image = Image.new("RGB", (64, 64), "#0a0f1c")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, 56, 56), fill="#00ffcc")
    draw.text((23, 20), "K", fill="black")
    return image

def tray_show(icon, item):
    show_window()

def tray_hide(icon, item):
    hide_window()

def tray_disable(icon, item):
    disable_and_exit()

def tray_exit(icon, item):
    exit_current_session()

def start_tray():
    global tray_icon
    if not TRAY_AVAILABLE:
        return
    menu = pystray.Menu(
        pystray.MenuItem("Show", tray_show),
        pystray.MenuItem("Hide", tray_hide),
        pystray.MenuItem("Exit", tray_exit),
        pystray.MenuItem("Disable & Exit", tray_disable),
    )
    tray_icon = pystray.Icon("KyraAssistant", create_tray_image(), "Kyra Assistant", menu)
    tray_icon.run()

# =======================
# COMMAND ENGINE
# =======================
def process_command(cmd: str):
    global pending_action, file_data, listening_active

    raw = cmd.strip().lower()
    norm = normalize_text(raw)

    # Smart confirmation
    if pending_action in ["shutdown", "restart", "lock", "sleep"]:
        YES_WORDS = ["yes", "yeah", "sure", "ok", "okay", "confirm", "proceed", "do it"]
        NO_WORDS = ["no", "cancel", "dont", "don't", "stop", "leave it"]

        if any(has_phrase(norm, w) for w in YES_WORDS):
            action = pending_action
            pending_action = None
            return run_system(action)

        if any(has_phrase(norm, w) for w in NO_WORDS):
            pending_action = None
            return "Cancelled"

        return "Please say yes or no"

    # Stop listening only
    if has_phrase(norm, "stop listening"):
        listening_active = False
        ui_set(status_label, "● IDLE", "cyan")
        return "Stopped listening"

    # Exit current session only
    if has_phrase(norm, "close kyra") or norm == "exit":
        exit_current_session()
        return "Closing Kyra"

    # Create flow
    if pending_action == "ask_location":
        file_data["path"] = get_path(raw)
        pending_action = "ask_type"
        return "Should I create a file or a folder?"

    if pending_action == "ask_type":
        if has_word(norm, "file") or has_word(norm, "text") or has_word(norm, "document"):
            file_data["type"] = "file"
        elif has_word(norm, "folder"):
            file_data["type"] = "folder"
        else:
            return "Please say file or folder"
        pending_action = "ask_name"
        return "What should be the name?"

    if pending_action == "ask_name":
        file_data["name"] = raw
        pending_action = None
        return create_item()

    # Quick create
    if raw.startswith("create file"):
        name = raw.replace("create file", "").strip()
        if not name:
            pending_action = "ask_location"
            file_data = {}
            return "Where should I create it?"
        file_data = {"path": last_path or os.getcwd(), "type": "file", "name": name}
        return create_item()

    if raw.startswith("create folder"):
        name = raw.replace("create folder", "").strip()
        if not name:
            pending_action = "ask_location"
            file_data = {}
            return "Where should I create it?"
        file_data = {"path": last_path or os.getcwd(), "type": "folder", "name": name}
        return create_item()

    if has_phrase(norm, "create new") or norm == "create":
        file_data = {}
        pending_action = "ask_location"
        return "Where should I create it?"

    # Search file
    if raw.startswith("find "):
        query = raw.replace("find ", "").strip()
        return search_file(query)

    if raw.startswith("search file "):
        query = raw.replace("search file ", "").strip()
        return search_file(query)

    # Show files
    if has_phrase(norm, "show my files"):
        try:
            os.startfile(os.getcwd())
            return "Showing files"
        except Exception:
            return "Could not open current folder"

    # Search web
    if raw.startswith("search for "):
        query = raw.replace("search for ", "").strip()
        return google_search(query)

    if raw.startswith("search youtube for "):
        query = raw.replace("search youtube for ", "").strip()
        webbrowser.open(f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}")
        return f"Searching YouTube for {query}"

    # Open applications
    if raw.startswith("open "):
        app = raw.replace("open ", "").strip()
        res = open_application(app)
        if res:
            return res
        return google_search(app)

    # Protected system commands
    if has_word(norm, "shutdown"):
        pending_action = "shutdown"
        return "Are you sure you want to shutdown?"

    if has_word(norm, "restart"):
        pending_action = "restart"
        return "Are you sure you want to restart?"

    if has_word(norm, "lock"):
        pending_action = "lock"
        return "Are you sure you want to lock the system?"

    if has_word(norm, "sleep"):
        pending_action = "sleep"
        return "Are you sure you want to sleep the system?"

    # Basics
    if has_word(norm, "time"):
        return datetime.datetime.now().strftime("Time is %H:%M")

    if has_word(norm, "date"):
        return datetime.datetime.now().strftime("%d %B %Y")

    if has_word(norm, "hello"):
        return "Hello, I am Kyra"

    return "Command not recognized"

# =======================
# CONVERSATION
# =======================
def conversation():
    global listening_active, last_activity_time

    with sr.Microphone() as source:
        try:
            r.adjust_for_ambient_noise(source, duration=0.5)
        except Exception:
            pass

        ui_set(status_label, "● LISTENING", "green")

        while listening_active:
            try:
                audio = r.listen(source, timeout=5, phrase_time_limit=5)
                cmd = r.recognize_google(audio)
                ui_set(user_label, f"👤 {cmd}")

                res = process_command(cmd)
                speak(res)

                last_activity_time = time.time()
            except sr.WaitTimeoutError:
                pass
            except Exception as e:
                print("Speech Error:", e)

            if time.time() - last_activity_time > AUTO_SLEEP_SECONDS:
                break

    listening_active = False
    ui_set(status_label, "● IDLE", "cyan")
    hide_window()

# =======================
# WAKE WORD
# =======================
porcupine = pvporcupine.create(
    access_key=ACCESS_KEY,
    keyword_paths=[KEYWORD_PATH]
)

pa = pyaudio.PyAudio()

stream = pa.open(
    rate=porcupine.sample_rate,
    channels=1,
    format=pyaudio.paInt16,
    input=True,
    frames_per_buffer=porcupine.frame_length,
)

def wake():
    global listening_active, last_activity_time

    while True:
        try:
            pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

            if porcupine.process(pcm) >= 0:
                if not listening_active:
                    listening_active = True
                    last_activity_time = time.time()

                    show_window()
                    speak("Yes, I am listening")
                    threading.Thread(target=conversation, daemon=True).start()
        except Exception as e:
            print("Wake error:", e)
            time.sleep(0.1)

# =======================
# START
# =======================
if TRAY_AVAILABLE:
    threading.Thread(target=start_tray, daemon=True).start()

threading.Thread(target=wake, daemon=True).start()
root.mainloop()
