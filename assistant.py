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

# =======================
# RESOURCE PATH FIX (EXE)
# =======================
def get_resource_path(filename):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.abspath("."), filename)

KEYWORD_PATH = get_resource_path("kyra.ppn")

# =======================
# ACCESS KEY (UI INPUT)
# =======================
def get_access_key():
    if os.path.exists("config.txt"):
        with open("config.txt", "r") as f:
            return f.read().strip()

    def save_key():
        key = entry.get()
        with open("config.txt", "w") as f:
            f.write(key)
        popup.destroy()

    popup = tk.Tk()
    popup.title("Enter Access Key")
    popup.geometry("300x150")

    tk.Label(popup, text="Enter Porcupine Access Key").pack(pady=10)
    entry = tk.Entry(popup, width=30)
    entry.pack()
    tk.Button(popup, text="Save", command=save_key).pack(pady=10)

    popup.mainloop()

    return get_access_key()

ACCESS_KEY = get_access_key()

# =======================
# AUTO START (STARTUP)
# =======================
def add_to_startup():
    try:
        startup = os.path.join(os.getenv('APPDATA'),
                              'Microsoft\\Windows\\Start Menu\\Programs\\Startup')

        exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)

        dest = os.path.join(startup, "KyraAssistant.exe")

        if not os.path.exists(dest):
            shutil.copy(exe_path, dest)
    except:
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

# =======================
# SPEAK
# =======================
tts_lock = threading.Lock()

def speak(text):
    def run():
        try:
            with tts_lock:
                ai_label.config(text="🤖 " + text)
                engine = pyttsx3.init()
                engine.setProperty('rate', 175)
                engine.say(text)
                engine.runAndWait()
        except:
            pass
    threading.Thread(target=run, daemon=True).start()

# =======================
# UI
# =======================
root = tk.Tk()
root.title("KYRA AI")
root.geometry("320x180")
root.configure(bg="#0a0f1c")
root.attributes("-topmost", True)
root.overrideredirect(True)

frame = tk.Frame(root, bg="#0a0f1c")
frame.pack(fill="both", expand=True)

status_label = tk.Label(frame, text="● IDLE", fg="cyan", bg="#0a0f1c")
status_label.pack(pady=5)

user_label = tk.Label(frame, text="", fg="white", bg="#0a0f1c")
user_label.pack()

ai_label = tk.Label(frame, text="KYRA READY", fg="#00ffcc", bg="#0a0f1c")
ai_label.pack()

def drag(e):
    root.geometry(f"+{e.x_root}+{e.y_root}")

frame.bind("<B1-Motion>", drag)
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
def google_search(q):
    webbrowser.open(f"https://www.google.com/search?q={q}")
    return f"Searching {q}"

def get_path(text):
    if "desktop" in text:
        return os.path.join(os.path.expanduser("~"), "Desktop")
    if "documents" in text:
        return os.path.join(os.path.expanduser("~"), "Documents")
    if "downloads" in text:
        return os.path.join(os.path.expanduser("~"), "Downloads")
    return os.getcwd()

# =======================
# CREATE
# =======================
def create_item():
    global last_path
    try:
        path = file_data["path"]
        name = file_data["name"].replace(" ", "_")
        t = file_data["type"]
        last_path = path

        if t == "file":
            if "." not in name:
                name += ".txt"
            full = os.path.join(path, name)
            with open(full, "w") as f:
                f.write("Created by Kyra")

        elif t == "folder":
            full = os.path.join(path, name)
            os.makedirs(full, exist_ok=True)

        os.startfile(full)
        return f"{t} created successfully"

    except Exception as e:
        print(e)
        return "Error creating item"

# =======================
# OPEN APP
# =======================
def open_app(name):
    try:
        if "chrome" in name:
            os.system("start chrome")
            return "Opening Chrome"

        if "edge" in name:
            os.system("start msedge")
            return "Opening Edge"

        if "notepad" in name:
            os.system("notepad")
            return "Opening Notepad"

        if "youtube" in name:
            webbrowser.open("https://youtube.com")
            return "Opening YouTube"

        start_menu = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")

        for root_dir, _, files in os.walk(start_menu):
            for file in files:
                if name in file.lower():
                    os.startfile(os.path.join(root_dir, file))
                    return f"Opening {file}"

        return None

    except:
        return None

# =======================
# SYSTEM ACTION
# =======================
def run_action(action):
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

# =======================
# COMMAND ENGINE
# =======================
def process_command(cmd):
    global pending_action, file_data

    cmd = cmd.lower()

    if pending_action in ["shutdown", "restart", "lock", "sleep"]:
        YES = ["yes", "yeah", "sure", "ok", "do it", "confirm"]
        NO = ["no", "cancel", "stop", "don't"]

        if any(w in cmd for w in YES):
            action = pending_action
            pending_action = None
            return run_action(action)

        if any(w in cmd for w in NO):
            pending_action = None
            return "Cancelled"

        return "Please say yes or no"

    if "search for" in cmd:
        return google_search(cmd.replace("search for", ""))

    if "open" in cmd:
        app = cmd.replace("open", "").strip()
        res = open_app(app)
        if res:
            return res
        return google_search(app)

    if "shutdown" in cmd:
        pending_action = "shutdown"
        return "Are you sure?"

    if "restart" in cmd:
        pending_action = "restart"
        return "Are you sure?"

    if "lock" in cmd:
        pending_action = "lock"
        return "Are you sure?"

    if "sleep" in cmd:
        pending_action = "sleep"
        return "Are you sure?"

    if "time" in cmd:
        return datetime.datetime.now().strftime("Time is %H:%M")

    return "Command not recognized"

# =======================
# CONVERSATION
# =======================
def conversation():
    global listening_active, last_activity_time

    with sr.Microphone() as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
        status_label.config(text="● LISTENING", fg="green")

        while listening_active:
            try:
                audio = r.listen(source, timeout=5)
                cmd = r.recognize_google(audio)

                user_label.config(text="👤 " + cmd)

                res = process_command(cmd)
                speak(res)

                last_activity_time = time.time()

            except:
                pass

            if time.time() - last_activity_time > AUTO_SLEEP_SECONDS:
                break

    listening_active = False
    root.withdraw()

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
    frames_per_buffer=porcupine.frame_length
)

def wake():
    global listening_active, last_activity_time

    while True:
        pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
        pcm = struct.unpack_from("h"*porcupine.frame_length, pcm)

        if porcupine.process(pcm) >= 0:
            if not listening_active:
                listening_active = True
                last_activity_time = time.time()

                root.deiconify()
                speak("Yes I am listening")

                threading.Thread(target=conversation, daemon=True).start()

# =======================
# START
# =======================
threading.Thread(target=wake, daemon=True).start()
root.mainloop()