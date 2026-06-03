"""
站立提醒器 — 每 30 分钟弹出桌面通知，提醒你站起来活动一下。
运行后最小化到系统托盘，右键可退出。
"""

import time
import datetime
import threading
import sys
import os
import math
import json
import subprocess
import tempfile
import winsound
import queue
import tkinter as tk

# ——— 修复 Windows GBK 编码 ———————————————————————
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ——— 配置 ———————————————————————————————————————
INTERVAL_MINUTES = 30
SNOOZE_MINUTES   = 5
POSTURE_REMINDER_MINUTES = 5
MAX_SNOOZE_COUNT = 2
SHOW_ON_START    = False
IDLE_PAUSE_MINUTES = 2   # [CLAUDE] 空闲超过此时间冻结久坐计时
IDLE_RESET_MINUTES = 15  # [CLAUDE] 空闲超过此时间清零（真离开了）
FORCED_REST_AFTER_SNOOZES = 2
FORCED_REST_SECONDS = 60
LOCK_SCREEN_ON_SNOOZE_NUMBER = 3
ENABLE_RED_HEAT = False
USE_LAYERED_POPUP = False
USE_QT_POPUP = True
APP_NAME = "站立提醒器"
# [CLAUDE] 支持 PyInstaller 打包：exe 运行时从临时目录读资源
if getattr(sys, "frozen", False):
    APP_DIR = sys._MEIPASS
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
PET_ASSET_DIR    = os.path.join(APP_DIR, "assets", "pet")
SOUND_ASSET_DIR  = os.path.join(APP_DIR, "assets", "sounds")
EFFECT_ASSET_DIR = os.path.join(APP_DIR, "assets", "effects")
SETTINGS_PATH    = os.path.join(APP_DIR, "settings.json")
DEFAULT_RINGTONE_PATH = os.path.join(SOUND_ASSET_DIR, "mixkit-guitar-notification-alert-2320.wav")
DEFAULT_EFFECT_PATH = os.path.join(EFFECT_ASSET_DIR, "mixkit-lightning-whip-1508.wav")
POKE_EFFECT_PATH = DEFAULT_EFFECT_PATH
_RINGTONE_PATH    = DEFAULT_RINGTONE_PATH
_SOUND_TIMEOUT    = 300   # 铃声最长 5 分钟
_CHECK_EVERY_MS   = 5000  # 每 5 秒检查一次是否该提醒

# ——— 全局状态 —————————————————————————————————————
_active_seconds = 0     # [CLAUDE] 累计活跃坐姿秒数（空闲不计）
_next_reminder_at = 0   # 下次提醒的时间戳
_sound_on         = False
_effect_cache     = {}
_effect_queue     = queue.Queue(maxsize=1)
_effect_worker_started = False


# ╔══════════════════════════════════════════════════════╗
# ║  空闲检测（人不在电脑前暂停计时）                    ║
# ╚══════════════════════════════════════════════════════╝
def get_idle_seconds():
    """返回用户多久没动鼠标/键盘（秒），调用 Windows API。"""
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    tick = ctypes.windll.kernel32.GetTickCount()
    return max(0.0, (tick - lii.dwTime) / 1000.0)


def is_workstation_locked():
    """检测 Windows 是否处于锁屏状态（Win+L）。锁屏 = 人肯定不在。"""
    import ctypes

    try:
        hd = ctypes.windll.user32.OpenDesktopW("Default", 0, False, 1)  # DESKTOP_READOBJECTS
        if hd:
            ctypes.windll.user32.CloseDesktop(hd)
            return False
        return True
    except Exception:
        return False


def is_fullscreen_active():
    """检测前台窗口是否处于真正的全屏状态（无边框视频/游戏/PPT）。
    只检查尺寸会误判最大化的浏览器窗口，所以还要看窗口风格。"""
    import ctypes

    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 400 or h < 300:
            return False

        # 获取显示器尺寸
        monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint),
                        ("rcMonitor", RECT), ("rcWork", RECT),
                        ("dwFlags", ctypes.c_uint)]
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(mi))
        mw = mi.rcMonitor.right - mi.rcMonitor.left
        mh = mi.rcMonitor.bottom - mi.rcMonitor.top

        if w < mw or h < mh:
            return False

        # 精准判定：真正全屏的窗口通常是 WS_POPUP 且无标题栏/无边框
        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        # 无边框且覆盖全屏 = 全屏视频/游戏/PPT
        has_caption_or_border = bool(style & (WS_CAPTION | WS_THICKFRAME))
        if not has_caption_or_border:
            return True

        # 有边框但尺寸基本覆盖全屏（允许 8px 误差，某些播放器边框很细）
        return w >= mw - 8 and h >= mh - 8
    except Exception:
        return False


def lock_workstation():
    """Lock Windows when the optional strict snooze policy is enabled."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.LockWorkStation())
    except Exception:
        return False


# ╔══════════════════════════════════════════════════════╗
# ║  高清 DPI 支持                                       ║
# ╚══════════════════════════════════════════════════════╝
def enable_dpi_awareness():
    """避免 Windows 在高缩放屏幕上把 Tk 窗口当低清位图放大。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # Windows 10 1607+：按每个显示器使用真实 DPI，清晰度最好。
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
    except Exception:
        pass

    try:
        import ctypes
        shcore = ctypes.windll.shcore
        # Windows 8.1+ fallback：Per-monitor DPI aware。
        shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def tune_tk_scaling(root):
    """让 Tk 字体按系统 DPI 渲染，减少发虚和尺寸漂移。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForWindow(root.winfo_id())
        root.tk.call("tk", "scaling", dpi / 72)
    except Exception:
        pass


def get_ui_scale(root):
    """返回系统缩放比例，例如 125% 屏幕返回约 1.25。"""
    try:
        return max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
    except Exception:
        return 1.0


def scaled_font(family, size, weight=None):
    if weight:
        return (family, size, weight)
    return (family, size)


# ╔══════════════════════════════════════════════════════╗
# ║  防止多开                                            ║
# ╚══════════════════════════════════════════════════════╝
def already_running():
    import ctypes
    mutex_name = r"Global\StandUpReminder_Mutex"
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, True, mutex_name)
    return kernel32.GetLastError() == 183


# ╔══════════════════════════════════════════════════════╗
# ║  铃声控制                                            ║
# ╚══════════════════════════════════════════════════════╝
def load_settings():
    global _RINGTONE_PATH, POKE_EFFECT_PATH
    _RINGTONE_PATH = DEFAULT_RINGTONE_PATH
    POKE_EFFECT_PATH = DEFAULT_EFFECT_PATH
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        ringtone = data.get("ringtone_path")
        if ringtone and os.path.exists(ringtone):
            _RINGTONE_PATH = os.path.abspath(ringtone)
        effect = data.get("effect_path")
        if effect and os.path.exists(effect):
            POKE_EFFECT_PATH = os.path.abspath(effect)
    except Exception:
        _RINGTONE_PATH = DEFAULT_RINGTONE_PATH
        POKE_EFFECT_PATH = DEFAULT_EFFECT_PATH
    preload_effect(POKE_EFFECT_PATH)


def save_settings():
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ringtone_path": _RINGTONE_PATH,
                    "effect_path": POKE_EFFECT_PATH,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def get_ringtone_paths():
    paths = []
    seen = set()
    if os.path.exists(DEFAULT_RINGTONE_PATH):
        default_path = os.path.abspath(DEFAULT_RINGTONE_PATH)
        paths.append(default_path)
        seen.add(default_path)
    if os.path.isdir(SOUND_ASSET_DIR):
        for name in sorted(os.listdir(SOUND_ASSET_DIR), key=str.lower):
            if not name.lower().endswith(".wav"):
                continue
            path = os.path.abspath(os.path.join(SOUND_ASSET_DIR, name))
            if path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def ringtone_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def get_effect_paths():
    paths = []
    seen = set()
    if os.path.exists(DEFAULT_EFFECT_PATH):
        default_path = os.path.abspath(DEFAULT_EFFECT_PATH)
        paths.append(default_path)
        seen.add(default_path)
    if os.path.isdir(EFFECT_ASSET_DIR):
        for name in sorted(os.listdir(EFFECT_ASSET_DIR), key=str.lower):
            if not name.lower().endswith(".wav"):
                continue
            path = os.path.abspath(os.path.join(EFFECT_ASSET_DIR, name))
            if path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def effect_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def set_ringtone(path, preview=False):
    global _RINGTONE_PATH
    if not path or not os.path.exists(path):
        return False
    was_playing = _sound_on
    stop_sound()
    _RINGTONE_PATH = os.path.abspath(path)
    save_settings()
    if preview or was_playing:
        start_sound()
    if preview:
        threading.Timer(2.4, stop_sound).start()
    return True


def set_interaction_effect(path, preview=False):
    global POKE_EFFECT_PATH
    if not path or not os.path.exists(path):
        return False
    POKE_EFFECT_PATH = os.path.abspath(path)
    preload_effect(POKE_EFFECT_PATH)
    save_settings()
    if preview:
        play_effect_once(POKE_EFFECT_PATH)
    return True


def open_ringtone_folder():
    try:
        os.makedirs(SOUND_ASSET_DIR, exist_ok=True)
        os.startfile(SOUND_ASSET_DIR)
    except Exception:
        pass


def open_effect_folder():
    try:
        os.makedirs(EFFECT_ASSET_DIR, exist_ok=True)
        os.startfile(EFFECT_ASSET_DIR)
    except Exception:
        pass


def python_launcher():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


def qt_creation_flags():
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def write_anchor_file(path, anchor):
    if not path or anchor is None:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{float(anchor[0]):.2f},{float(anchor[1]):.2f}")
    except Exception:
        pass


def make_anchor_file(anchor=None):
    fd, path = tempfile.mkstemp(prefix="standup_anchor_", suffix=".txt")
    os.close(fd)
    write_anchor_file(path, anchor)
    return path


def run_qt_popup_process(anchor=None, snooze_enabled=True, refresh_callback=None,
                         anchor_provider=None):
    fd, result_path = tempfile.mkstemp(prefix="standup_qt_popup_", suffix=".txt")
    os.close(fd)
    anchor_path = make_anchor_file(anchor)
    args = [
        python_launcher(),
        os.path.join(APP_DIR, "qt_popup_demo.py"),
        "--popup",
        "--result-file", result_path,
        "--minutes", str(INTERVAL_MINUTES),
        "--snooze-minutes", str(SNOOZE_MINUTES),
        "--timeout", str(_SOUND_TIMEOUT),
        "--anchor-file", anchor_path,
    ]
    if not snooze_enabled:
        args.append("--no-snooze")
    if anchor is not None:
        args.extend(["--anchor-x", str(anchor[0]), "--anchor-y", str(anchor[1])])

    try:
        process = subprocess.Popen(
            args,
            cwd=APP_DIR,
            creationflags=qt_creation_flags(),
        )
        deadline = time.monotonic() + _SOUND_TIMEOUT + 8
        last_anchor_write = 0.0
        while process.poll() is None and time.monotonic() < deadline:
            if refresh_callback:
                try:
                    refresh_callback()
                except Exception:
                    pass
            if anchor_provider:
                now = time.monotonic()
                if now - last_anchor_write >= 0.033:
                    last_anchor_write = now
                    try:
                        write_anchor_file(anchor_path, anchor_provider())
                    except Exception:
                        pass
            time.sleep(0.016)
        if process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result = f.read().strip()
        except Exception:
            result = "done"
        return result if result in ("done", "snooze") else "done"
    finally:
        try:
            os.remove(result_path)
        except OSError:
            pass
        try:
            os.remove(anchor_path)
        except OSError:
            pass


def start_sound():
    global _sound_on
    _sound_on = True
    try:
        winsound.PlaySound(_RINGTONE_PATH,
                           winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


def play_effect_once(path):
    if not path or not os.path.exists(path):
        return
    data = _effect_cache.get(os.path.abspath(path))
    if data:
        start_effect_worker()
        try:
            _effect_queue.put_nowait(data)
        except queue.Full:
            pass
        return

    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


def start_effect_worker():
    global _effect_worker_started
    if _effect_worker_started:
        return
    _effect_worker_started = True

    def worker():
        while True:
            data = _effect_queue.get()
            try:
                winsound.PlaySound(data, winsound.SND_MEMORY)
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()


def preload_effect(path):
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            _effect_cache[os.path.abspath(path)] = f.read()
    except Exception:
        pass


preload_effect(POKE_EFFECT_PATH)
start_effect_worker()


def stop_sound():
    global _sound_on
    _sound_on = False
    try:
        winsound.PlaySound(None, 0)
    except Exception:
        pass


# ╔══════════════════════════════════════════════════════╗
# ║  克制的原生风格弹窗（Tkinter，主线程调用）           ║
# ╚══════════════════════════════════════════════════════╝
def rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """在 Canvas 上画圆角矩形，并返回组成它的 item id。"""
    radius = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    items = [
        canvas.create_arc(x1, y1, x1 + radius * 2, y1 + radius * 2,
                          start=90, extent=90, style="pieslice", **kwargs),
        canvas.create_arc(x2 - radius * 2, y1, x2, y1 + radius * 2,
                          start=0, extent=90, style="pieslice", **kwargs),
        canvas.create_arc(x2 - radius * 2, y2 - radius * 2, x2, y2,
                          start=270, extent=90, style="pieslice", **kwargs),
        canvas.create_arc(x1, y2 - radius * 2, x1 + radius * 2, y2,
                          start=180, extent=90, style="pieslice", **kwargs),
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, **kwargs),
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, **kwargs),
    ]
    return items


def create_canvas_button(canvas, x, y, width, height, text, command,
                         bg, fg, hover_bg, font, radius=18):
    tag = f"button_{x}_{y}_{width}_{height}"
    rect_items = rounded_rect(canvas, x, y, x + width, y + height, radius,
                              fill=bg, outline=bg, tags=(tag,))
    canvas.create_text(x + width / 2, y + height / 2, text=text, fill=fg,
                       font=font, tags=(tag,))

    def paint(color):
        for item in rect_items:
            canvas.itemconfigure(item, fill=color, outline=color)

    canvas.tag_bind(tag, "<Enter>", lambda _: (paint(hover_bg),
                                               canvas.configure(cursor="hand2")))
    canvas.tag_bind(tag, "<Leave>", lambda _: (paint(bg),
                                               canvas.configure(cursor="")))
    canvas.tag_bind(tag, "<Button-1>", lambda _: command())


def _hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _blend_color(start, end, t):
    sr, sg, sb = _hex_to_rgb(start)
    er, eg, eb = _hex_to_rgb(end)
    rgb = (
        round(sr + (er - sr) * t),
        round(sg + (eg - sg) * t),
        round(sb + (eb - sb) * t),
    )
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _gradient_color(colors, t):
    colors = list(colors)
    if len(colors) == 1:
        return colors[0]
    position = max(0.0, min(1.0, t)) * (len(colors) - 1)
    index = min(int(position), len(colors) - 2)
    local_t = position - index
    return _blend_color(colors[index], colors[index + 1], local_t)


def draw_vertical_gradient(canvas, x1, y1, x2, y2, colors, steps=96):
    for i in range(steps):
        t = i / max(steps - 1, 1)
        y_start = y1 + (y2 - y1) * i / steps
        y_end = y1 + (y2 - y1) * (i + 1) / steps
        color = _gradient_color(colors, t)
        canvas.create_rectangle(x1, y_start, x2, y_end,
                                fill=color, outline=color)


def draw_horizontal_gradient(canvas, x1, y1, x2, y2, colors, steps=160):
    for i in range(steps):
        t = i / max(steps - 1, 1)
        x_start = x1 + (x2 - x1) * i / steps
        x_end = x1 + (x2 - x1) * (i + 1) / steps
        color = _gradient_color(colors, t)
        canvas.create_rectangle(x_start, y1, x_end, y2,
                                fill=color, outline=color)


def draw_gradient_rounded_rect(canvas, x, y, width, height, radius,
                               colors, tag):
    items = []
    for i in range(max(1, int(width))):
        t = i / max(width - 1, 1)
        x1, x2 = x + i, x + i + 1
        xc = x + i + 0.5
        y_offset = 0
        if xc < x + radius:
            dx = x + radius - xc
            y_offset = radius - math.sqrt(max(0, radius * radius - dx * dx))
        elif xc > x + width - radius:
            dx = xc - (x + width - radius)
            y_offset = radius - math.sqrt(max(0, radius * radius - dx * dx))
        color = _gradient_color(colors, t)
        items.append(canvas.create_rectangle(
            x1, y + y_offset, x2, y + height - y_offset,
            fill=color, outline=color, tags=(tag,)
        ))
    return items


def create_gradient_button(canvas, x, y, width, height, text, command,
                           colors, hover_colors, fg, font,
                           radius=12):
    tag = f"gradient_button_{x}_{y}_{width}_{height}"
    rect_items = draw_gradient_rounded_rect(canvas, x, y, width, height,
                                            radius, colors, tag)
    canvas.create_text(x + width / 2, y + height / 2, text=text, fill=fg,
                       font=font, tags=(tag,))

    def paint(next_colors):
        for index, item in enumerate(rect_items):
            t = index / max(len(rect_items) - 1, 1)
            color = _gradient_color(next_colors, t)
            canvas.itemconfigure(item, fill=color, outline=color)

    canvas.tag_bind(tag, "<Enter>", lambda _: (paint(hover_colors),
                                               canvas.configure(cursor="hand2")))
    canvas.tag_bind(tag, "<Leave>", lambda _: (paint(colors),
                                               canvas.configure(cursor="")))
    canvas.tag_bind(tag, "<Button-1>", lambda _: command())


def render_gradient_rect(width, height, colors):
    from PIL import Image

    width = max(1, int(width))
    height = max(1, int(height))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = img.load()
    for x in range(width):
        color = _hex_to_rgb(_gradient_color(colors, x / max(width - 1, 1)))
        for y in range(height):
            pixels[x, y] = (*color, 255)
    return img


def render_rounded_rect(width, height, radius, fill, outline=None,
                        outline_width=0, scale=1):
    from PIL import Image, ImageDraw

    high_scale = max(3, round(scale * 3))
    hw, hh = max(1, round(width * high_scale)), max(1, round(height * high_scale))
    img = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, hw - 1, hh - 1],
        radius=round(radius * high_scale),
        fill=fill,
    )
    if outline:
        border = max(1, round(outline_width * high_scale))
        inset = max(border, round(high_scale * 0.55))
        draw.rounded_rectangle(
            [inset, inset, hw - 1 - inset, hh - 1 - inset],
            radius=max(1, round(radius * high_scale) - inset),
            outline=outline,
            width=border,
        )
    return img.resize((round(width), round(height)), Image.Resampling.LANCZOS)


def render_speech_bubble(width, height, radius, tail_x, tail_width, tail_height,
                         fill, outline=None, outline_width=1, scale=1):
    from PIL import Image, ImageDraw

    high_scale = max(3, round(scale * 3))
    hw, hh = max(1, round(width * high_scale)), max(1, round(height * high_scale))
    bubble_h = max(1, round((height - tail_height) * high_scale))
    img = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    tail_center = round(tail_x * high_scale)
    tail_half = round(tail_width * high_scale / 2)
    tail_tip_y = hh - 1
    tail_base_y = bubble_h - 2
    tail = [
        (tail_center - tail_half, tail_base_y),
        (tail_center + tail_half, tail_base_y),
        (tail_center, tail_tip_y),
    ]

    draw.rounded_rectangle(
        [0, 0, hw - 1, bubble_h - 1],
        radius=round(radius * high_scale),
        fill=fill,
    )
    draw.polygon(tail, fill=fill)

    if outline:
        border = max(1, round(outline_width * high_scale))
        inset = max(border, round(high_scale * 0.55))
        draw.rounded_rectangle(
            [inset, inset, hw - 1 - inset, bubble_h - 1 - inset],
            radius=max(1, round(radius * high_scale) - inset),
            outline=outline,
            width=border,
        )
        tail_line = [
            (tail_center - tail_half + inset, tail_base_y - inset),
            (tail_center, tail_tip_y - inset),
            (tail_center + tail_half - inset, tail_base_y - inset),
        ]
        draw.line(tail_line, fill=outline, width=border, joint="curve")

    return img.resize((round(width), round(height)), Image.Resampling.LANCZOS)


def render_gradient_rounded_rect(width, height, radius, colors, scale=1):
    from PIL import Image, ImageDraw

    high_scale = max(3, round(scale * 3))
    hw, hh = max(1, round(width * high_scale)), max(1, round(height * high_scale))
    gradient = render_gradient_rect(hw, hh, colors)
    mask = Image.new("L", (hw, hh), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        [0, 0, hw - 1, hh - 1],
        radius=round(radius * high_scale),
        fill=255,
    )
    img = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    img.paste(gradient, (0, 0), mask)
    return img.resize((round(width), round(height)), Image.Resampling.LANCZOS)


def canvas_image(canvas, x, y, image):
    from PIL import ImageTk

    photo = ImageTk.PhotoImage(image)
    if not hasattr(canvas, "_image_refs"):
        canvas._image_refs = []
    canvas._image_refs.append(photo)
    return canvas.create_image(x, y, image=photo, anchor="nw")


def create_image_button(canvas, x, y, width, height, text, command,
                        bg, fg, hover_bg, font, radius, scale):
    from PIL import ImageTk

    tag = f"image_button_{x}_{y}_{width}_{height}"
    normal = ImageTk.PhotoImage(
        render_rounded_rect(width, height, radius, bg, scale=scale)
    )
    hover = ImageTk.PhotoImage(
        render_rounded_rect(width, height, radius, hover_bg, scale=scale)
    )
    if not hasattr(canvas, "_image_refs"):
        canvas._image_refs = []
    canvas._image_refs.extend([normal, hover])
    image_item = canvas.create_image(x, y, image=normal, anchor="nw", tags=(tag,))
    canvas.create_text(x + width / 2, y + height / 2, text=text, fill=fg,
                       font=font, tags=(tag,))

    canvas.tag_bind(tag, "<Enter>", lambda _:
                    (canvas.itemconfigure(image_item, image=hover),
                     canvas.configure(cursor="hand2")))
    canvas.tag_bind(tag, "<Leave>", lambda _:
                    (canvas.itemconfigure(image_item, image=normal),
                     canvas.configure(cursor="")))
    canvas.tag_bind(tag, "<Button-1>", lambda _: command())


def create_image_gradient_button(canvas, x, y, width, height, text, command,
                                 colors, hover_colors, fg, font, radius, scale):
    from PIL import ImageTk

    tag = f"image_gradient_button_{x}_{y}_{width}_{height}"
    normal = ImageTk.PhotoImage(
        render_gradient_rounded_rect(width, height, radius, colors, scale=scale)
    )
    hover = ImageTk.PhotoImage(
        render_gradient_rounded_rect(width, height, radius, hover_colors, scale=scale)
    )
    if not hasattr(canvas, "_image_refs"):
        canvas._image_refs = []
    canvas._image_refs.extend([normal, hover])
    image_item = canvas.create_image(x, y, image=normal, anchor="nw", tags=(tag,))
    canvas.create_text(x + width / 2, y + height / 2, text=text, fill=fg,
                       font=font, tags=(tag,))

    canvas.tag_bind(tag, "<Enter>", lambda _:
                    (canvas.itemconfigure(image_item, image=hover),
                     canvas.configure(cursor="hand2")))
    canvas.tag_bind(tag, "<Leave>", lambda _:
                    (canvas.itemconfigure(image_item, image=normal),
                     canvas.configure(cursor="")))
    canvas.tag_bind(tag, "<Button-1>", lambda _: command())


def pil_font(size, bold=False):
    from PIL import ImageFont

    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhbd.ttc"]
    for name in names:
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_wrapped_text(draw, xy, text, font, fill, max_width, line_gap=3):
    x, y = xy
    line = ""
    for char in text:
        if char == "\n":
            draw.text((x, y), line, font=font, fill=fill)
            y += font.size + line_gap
            line = ""
            continue
        candidate = line + char
        if line and draw.textlength(candidate, font=font) > max_width:
            draw.text((x, y), line, font=font, fill=fill)
            y += font.size + line_gap
            line = char
        else:
            line = candidate
    if line:
        draw.text((x, y), line, font=font, fill=fill)


def draw_center_text(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2 - 1),
        text,
        font=font,
        fill=fill,
    )


def apply_layered_window_image(window, image, x, y):
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.restype = wintypes.BOOL
    hwnd = window.winfo_id()
    width, height = image.size

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    ULW_ALPHA = 0x00000002
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    AC_SRC_OVER = 0
    AC_SRC_ALPHA = 1

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_ubyte),
            ("BlendFlags", ctypes.c_ubyte),
            ("SourceConstantAlpha", ctypes.c_ubyte),
            ("AlphaFormat", ctypes.c_ubyte),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", ctypes.c_ubyte),
            ("rgbGreen", ctypes.c_ubyte),
            ("rgbRed", ctypes.c_ubyte),
            ("rgbReserved", ctypes.c_ubyte),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]

    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
        wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
    ]

    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    ex_style = get_long(hwnd, GWL_EXSTYLE)
    set_long(hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED)

    rgba = image.convert("RGBA")
    bgra = bytearray()
    for r, g, b, a in rgba.getdata():
        bgra.extend((b * a // 255, g * a // 255, r * a // 255, a))

    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bits = ctypes.c_void_p()
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    bmi.bmiHeader.biSizeImage = len(bgra)
    hbitmap = gdi32.CreateDIBSection(
        hdc_screen, ctypes.byref(bmi), DIB_RGB_COLORS,
        ctypes.byref(bits), None, 0,
    )
    if not hbitmap:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
        return False

    ctypes.memmove(bits, bytes(bgra), len(bgra))
    old_bitmap = gdi32.SelectObject(hdc_mem, hbitmap)
    pt_dst = POINT(x, y)
    pt_src = POINT(0, 0)
    size = SIZE(width, height)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

    ok = bool(user32.UpdateLayeredWindow(
        hwnd, hdc_screen, ctypes.byref(pt_dst), ctypes.byref(size),
        hdc_mem, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA,
    ))

    gdi32.SelectObject(hdc_mem, old_bitmap)
    gdi32.DeleteObject(hbitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(None, hdc_screen)
    return ok


def apply_bubble_window_region(window, width, height, radius, tail_x, tail_width, tail_height):
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    hwnd = window.winfo_id()

    RGN_OR = 2
    ALTERNATE = 1

    POINT = wintypes.POINT
    body_h = max(1, height - tail_height)
    tail_base_y = max(0, body_h - 1)
    tail_tip_y = max(0, height - 1)
    points = (POINT * 3)(
        POINT(round(tail_x - tail_width / 2), tail_base_y),
        POINT(round(tail_x + tail_width / 2), tail_base_y),
        POINT(round(tail_x), tail_tip_y),
    )

    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    gdi32.CreateRoundRectRgn.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int,
    ]
    gdi32.CreatePolygonRgn.restype = wintypes.HRGN
    gdi32.CreatePolygonRgn.argtypes = [
        ctypes.POINTER(POINT), ctypes.c_int, ctypes.c_int,
    ]
    gdi32.CombineRgn.restype = ctypes.c_int
    gdi32.CombineRgn.argtypes = [
        wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int,
    ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.SetWindowRgn.restype = ctypes.c_int
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]

    bubble_rgn = gdi32.CreateRoundRectRgn(
        0, 0, width + 1, body_h + 1,
        radius * 2, radius * 2,
    )
    tail_rgn = gdi32.CreatePolygonRgn(points, 3, ALTERNATE)
    if not bubble_rgn or not tail_rgn:
        if bubble_rgn:
            gdi32.DeleteObject(bubble_rgn)
        if tail_rgn:
            gdi32.DeleteObject(tail_rgn)
        return False

    gdi32.CombineRgn(bubble_rgn, bubble_rgn, tail_rgn, RGN_OR)
    gdi32.DeleteObject(tail_rgn)
    if user32.SetWindowRgn(hwnd, bubble_rgn, True):
        return True
    gdi32.DeleteObject(bubble_rgn)
    return False


def apply_rounded_window_region(window, width, height, radius):
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    hwnd = window.winfo_id()
    gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    gdi32.CreateRoundRectRgn.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int,
    ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.SetWindowRgn.restype = ctypes.c_int
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    rgn = gdi32.CreateRoundRectRgn(
        0, 0, width + 1, height + 1, radius * 2, radius * 2,
    )
    if not rgn:
        return False
    if user32.SetWindowRgn(hwnd, rgn, True):
        return True
    gdi32.DeleteObject(rgn)
    return False


def apply_native_window_style(window):
    """尽量使用 Windows 11 的原生圆角和窗口背景；旧系统会自动忽略。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = window.winfo_id()
        dwmapi = ctypes.windll.dwmapi

        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(DWMWCP_ROUND),
            ctypes.sizeof(DWMWCP_ROUND),
        )

        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMSBT_MAINWINDOW = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(DWMSBT_MAINWINDOW),
            ctypes.sizeof(DWMSBT_MAINWINDOW),
        )
    except Exception:
        pass


def show_popup(parent, snooze_count=0, max_snoozes=MAX_SNOOZE_COUNT,
               anchor_window=None):
    """显示从桌宠引出的提醒气泡，返回 done 或 snooze。"""
    stop_sound()
    start_sound()

    snooze_locked = snooze_count >= max_snoozes
    if USE_QT_POPUP:
        try:
            # [CLAUDE] exe 模式不能 spawn 子进程，直接 import
            if getattr(sys, "frozen", False):
                from qt_popup_demo import show_qt_popup
                anchor = None
                if anchor_window is not None:
                    anchor_window.update_idletasks()
                    anchor = (
                        anchor_window.winfo_rootx() + anchor_window.winfo_width() * 0.50,
                        anchor_window.winfo_rooty() - 8,
                    )
                action = show_qt_popup(
                    minutes=INTERVAL_MINUTES,
                    snooze_minutes=SNOOZE_MINUTES,
                    snooze_enabled=not snooze_locked,
                    anchor=anchor,
                    timeout_seconds=_SOUND_TIMEOUT,
                )
            else:
                anchor = None
                anchor_provider = None
                if anchor_window is not None:
                    provider = getattr(anchor_window, "_pet_anchor_provider", None)
                    if callable(provider):
                        anchor_provider = lambda: provider("popup")
                        anchor = anchor_provider()
                    else:
                        def fallback_anchor():
                            anchor_window.update_idletasks()
                            return (
                                anchor_window.winfo_rootx() + anchor_window.winfo_width() * 0.50,
                                anchor_window.winfo_rooty() + anchor_window.winfo_height() * 0.52,
                            )
                        anchor_provider = fallback_anchor
                        anchor = fallback_anchor()
                action = run_qt_popup_process(
                    anchor,
                    snooze_enabled=not snooze_locked,
                    refresh_callback=parent.update,
                    anchor_provider=anchor_provider,
                )
            stop_sound()
            return action
        except Exception:
            stop_sound()
            start_sound()

    popup = tk.Toplevel(parent)
    popup.title(APP_NAME)
    popup.resizable(False, False)
    popup.attributes("-topmost", True)
    popup.overrideredirect(True)

    surface = "#FFFFFF"
    surface_subtle = "#F6F8FA"
    outline = "#E7EAEE"
    text_primary = "#202124"
    text_secondary = "#5F6368"
    text_muted = "#8A9099"
    io_gradient = ["#6D8FE6", "#A47ACF", "#D77D75", "#E5B35B", "#70A874"]
    button_gradient = ["#6D8FE6", "#8B79D2", "#6BAE8B"]
    button_gradient_hover = ["#5678CD", "#7460BD", "#558F72"]
    secondary = "#F1F3F4"
    secondary_hover = "#E8EAED"

    popup.configure(bg=surface)

    # Solid popup: avoids Tk color-key transparency jaggies on rounded corners.
    base_w, base_h = 300, 204
    tail_h = 18
    tail_x = base_w - 42
    label_y = 24
    title_y = 52
    body_y = 84
    rule_y = 124
    button_y = 140
    ui_scale = get_ui_scale(parent)
    scale = lambda value: round(value * ui_scale)
    w, h = round(base_w * ui_scale), round(base_h * ui_scale)
    sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()

    if anchor_window is not None:
        anchor_window.update_idletasks()
        target_x = anchor_window.winfo_rootx() + anchor_window.winfo_width() * 0.50
        target_y = anchor_window.winfo_rooty() - 8
        px = round(target_x - scale(tail_x))
        py = round(target_y - h)
        px = max(10, min(px, sw - w - 10))
        py = max(10, min(py, sh - h - 10))
    else:
        px, py = (sw - w) // 2, (sh - h) // 2

    popup.geometry(f"{w}x{h}+{px}+{py}")
    popup.update_idletasks()
    apply_native_window_style(popup)
    apply_bubble_window_region(
        popup, w, h, scale(24), scale(tail_x), scale(34), scale(tail_h),
    )

    if USE_LAYERED_POPUP and sys.platform == "win32":
        from PIL import ImageDraw

        result = {"value": "done"}
        hover = {"item": None}
        press = {"item": None}
        drag = {"offset": None}

        close_rect = (scale(base_w - 42), scale(18), scale(24), scale(24))
        snooze_rect = (scale(78), scale(button_y), scale(98), scale(32))
        done_x = 188 if not snooze_locked else 68
        done_w = 96 if not snooze_locked else 164
        done_rect = (scale(done_x), scale(button_y), scale(done_w), scale(32))

        def rect_contains(rect, x, y):
            rx, ry, rw, rh = rect
            return rx <= x <= rx + rw and ry <= y <= ry + rh

        def hit_test(x, y):
            if rect_contains(close_rect, x, y):
                return "close"
            if not snooze_locked and rect_contains(snooze_rect, x, y):
                return "snooze"
            if rect_contains(done_rect, x, y):
                return "done"
            return None

        def render_popup_layer():
            img = render_speech_bubble(
                w, h, scale(24), scale(tail_x), scale(34), scale(tail_h),
                surface, outline, 1, scale=ui_scale,
            )
            draw = ImageDraw.Draw(img)

            img.alpha_composite(
                render_gradient_rect(scale(base_w - 72), scale(2), io_gradient),
                (scale(36), scale(rule_y)),
            )

            close_bg = secondary_hover if hover["item"] == "close" else surface
            img.alpha_composite(
                render_rounded_rect(
                    close_rect[2], close_rect[3], scale(12), close_bg,
                    scale=ui_scale,
                ),
                (close_rect[0], close_rect[1]),
            )
            draw_center_text(
                draw,
                (close_rect[0], close_rect[1], close_rect[0] + close_rect[2],
                 close_rect[1] + close_rect[3]),
                "×",
                pil_font(scale(12)),
                text_muted,
            )

            mins_text = "1 分钟" if INTERVAL_MINUTES == 1 else f"{INTERVAL_MINUTES} 分钟"
            pill_x, pill_y = scale(base_w - 106), scale(22)
            pill_w, pill_h = scale(56), scale(24)
            img.alpha_composite(
                render_rounded_rect(
                    pill_w, pill_h, scale(12), surface_subtle,
                    outline, 1, scale=ui_scale,
                ),
                (pill_x, pill_y),
            )
            draw_center_text(
                draw, (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
                mins_text, pil_font(scale(8), bold=True), text_secondary,
            )

            draw.text(
                (scale(36), scale(label_y)), "久坐提醒",
                font=pil_font(scale(9), bold=True), fill=text_secondary,
            )
            draw.text(
                (scale(36), scale(title_y)), "该起身活动一下了",
                font=pil_font(scale(16), bold=True), fill=text_primary,
            )
            draw_wrapped_text(
                draw,
                (scale(36), scale(body_y)),
                "离开屏幕两分钟，伸展肩颈，走几步，顺手喝点水。",
                pil_font(scale(9)),
                text_secondary,
                scale(base_w - 72),
                line_gap=scale(2),
            )

            if not snooze_locked:
                snooze_bg = secondary_hover if hover["item"] == "snooze" else secondary
                img.alpha_composite(
                    render_rounded_rect(
                        snooze_rect[2], snooze_rect[3], scale(10),
                        snooze_bg, scale=ui_scale,
                    ),
                    (snooze_rect[0], snooze_rect[1]),
                )
                draw_center_text(
                    draw,
                    (snooze_rect[0], snooze_rect[1],
                     snooze_rect[0] + snooze_rect[2],
                     snooze_rect[1] + snooze_rect[3]),
                    f"稍后 {SNOOZE_MINUTES} 分钟",
                    pil_font(scale(9), bold=True),
                    "#3C4043",
                )

            img.alpha_composite(
                render_gradient_rounded_rect(
                    done_rect[2], done_rect[3], scale(10),
                    button_gradient_hover if hover["item"] == "done" else button_gradient,
                    scale=ui_scale,
                ),
                (done_rect[0], done_rect[1]),
            )
            draw_center_text(
                draw,
                (done_rect[0], done_rect[1],
                 done_rect[0] + done_rect[2], done_rect[1] + done_rect[3]),
                "我现在起身",
                pil_font(scale(10), bold=True),
                "#FFFFFF",
            )
            return img

        def redraw_layer(x=None, y=None):
            popup.update_idletasks()
            apply_layered_window_image(
                popup,
                render_popup_layer(),
                popup.winfo_x() if x is None else x,
                popup.winfo_y() if y is None else y,
            )

        def dismiss(value="done"):
            result["value"] = value
            stop_sound()
            try:
                popup.grab_release()
            except tk.TclError:
                pass
            popup.destroy()

        def on_motion(event):
            if drag["offset"] is not None:
                return
            item = hit_test(event.x, event.y)
            if item != hover["item"]:
                hover["item"] = item
                popup.configure(cursor="hand2" if item else "")
                redraw_layer()

        def on_press(event):
            item = hit_test(event.x, event.y)
            press["item"] = item
            if item is None:
                drag["offset"] = (
                    event.x_root - popup.winfo_x(),
                    event.y_root - popup.winfo_y(),
                )

        def on_drag(event):
            if drag["offset"] is None:
                return
            dx, dy = drag["offset"]
            nx, ny = event.x_root - dx, event.y_root - dy
            popup.geometry(f"+{nx}+{ny}")
            redraw_layer(nx, ny)

        def on_release(event):
            item = hit_test(event.x, event.y)
            was_pressed = press["item"]
            press["item"] = None
            drag["offset"] = None
            if item and item == was_pressed:
                dismiss("done" if item == "close" else item)

        popup.bind("<Motion>", on_motion)
        popup.bind("<Leave>", lambda _: (hover.update(item=None), redraw_layer()))
        popup.bind("<ButtonPress-1>", on_press)
        popup.bind("<B1-Motion>", on_drag)
        popup.bind("<ButtonRelease-1>", on_release)
        popup.protocol("WM_DELETE_WINDOW", lambda: dismiss("done"))
        popup.bind("<Escape>", lambda _: dismiss("done"))
        popup.after(_SOUND_TIMEOUT * 1000, lambda: dismiss("done"))

        redraw_layer(px, py)
        popup.focus_force()
        popup.grab_set()
        parent.wait_window(popup)
        return result["value"]

    canvas = tk.Canvas(popup, width=w, height=h, bg=surface,
                       highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    canvas_image(
        canvas, 0, 0,
        render_speech_bubble(
            w, h, scale(24), scale(tail_x), scale(34), scale(tail_h),
            surface, outline, 1, scale=ui_scale,
        ),
    )
    canvas_image(
        canvas,
        scale(36), scale(rule_y),
        render_gradient_rect(scale(base_w - 72), scale(2), io_gradient),
    )

    result = {"value": "done"}

    def dismiss(value="done"):
        result["value"] = value
        stop_sound()
        try:
            popup.grab_release()
        except tk.TclError:
            pass
        popup.destroy()

    close_tag = "close_button"
    close_bg = canvas_image(
        canvas,
        scale(base_w - 42), scale(18),
        render_rounded_rect(scale(24), scale(24), scale(12), surface, scale=ui_scale),
    )
    close_text = canvas.create_text(scale(base_w - 30), scale(29), text="×",
                                    font=("Segoe UI", 12), fill=text_muted,
                                    tags=(close_tag,))
    canvas.itemconfigure(close_bg, tags=(close_tag,))
    close_hover = canvas_image(
        canvas,
        -1000, -1000,
        render_rounded_rect(scale(24), scale(24), scale(12),
                            secondary_hover, scale=ui_scale),
    )
    close_hover_image = canvas.itemcget(close_hover, "image")
    canvas.delete(close_hover)
    close_normal_image = canvas.itemcget(close_bg, "image")

    def paint_close(color, text_color):
        canvas.itemconfigure(
            close_bg,
            image=close_hover_image if color == secondary_hover else close_normal_image,
        )
        canvas.itemconfigure(close_text, fill=text_color)

    canvas.tag_bind(close_tag, "<Enter>", lambda _:
                    (paint_close(secondary_hover, text_secondary),
                     canvas.configure(cursor="hand2")))
    canvas.tag_bind(close_tag, "<Leave>", lambda _:
                    (paint_close(surface, text_muted), canvas.configure(cursor="")))
    canvas.tag_bind(close_tag, "<Button-1>", lambda _: dismiss("done"))

    # 文案
    mins_text = "1 分钟" if INTERVAL_MINUTES == 1 else f"{INTERVAL_MINUTES} 分钟"
    canvas.create_text(scale(36), scale(label_y), text="久坐提醒",
                       anchor="nw",
                       font=("Microsoft YaHei UI", 9, "bold"),
                       fill=text_secondary)
    canvas_image(
        canvas,
        scale(base_w - 106), scale(22),
        render_rounded_rect(scale(56), scale(24), scale(12),
                            surface_subtle, outline, 1, scale=ui_scale),
    )
    canvas.create_text(scale(base_w - 78), scale(34), text=mins_text,
                       font=("Microsoft YaHei UI", 8, "bold"),
                       fill=text_secondary)

    canvas.create_text(scale(36), scale(title_y), text="该起身活动一下了",
                       anchor="nw",
                       font=("Microsoft YaHei UI", 16, "bold"),
                       fill=text_primary)
    body_text = canvas.create_text(
        scale(36), scale(body_y),
        text="离开屏幕两分钟，伸展肩颈，走几步，顺手喝点水。",
        anchor="nw", width=scale(base_w - 72),
        font=("Microsoft YaHei UI", 9),
        fill=text_secondary,
    )

    if not snooze_locked:
        create_image_button(
            canvas, scale(78), scale(button_y), scale(98), scale(32),
            f"稍后 {SNOOZE_MINUTES} 分钟",
            lambda: dismiss("snooze"),
            bg=secondary, fg="#3C4043", hover_bg=secondary_hover,
            font=("Microsoft YaHei UI", 9, "bold"),
            radius=scale(10),
            scale=ui_scale
        )
    done_x = 188 if not snooze_locked else 68
    done_w = 96 if not snooze_locked else 164
    create_image_gradient_button(
        canvas, scale(done_x), scale(button_y), scale(done_w), scale(32),
        "我现在起身",
        lambda: dismiss("done"),
        colors=button_gradient,
        hover_colors=button_gradient_hover,
        fg="#FFFFFF",
        font=("Microsoft YaHei UI", 10, "bold"),
        radius=scale(10),
        scale=ui_scale
    )

    def start_move(event):
        popup._drag_offset = (event.x_root - popup.winfo_x(),
                              event.y_root - popup.winfo_y())

    def do_move(event):
        dx, dy = getattr(popup, "_drag_offset", (0, 0))
        popup.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    canvas.bind("<ButtonPress-1>", start_move)
    canvas.bind("<B1-Motion>", do_move)

    # 超时 & 关闭
    popup.protocol("WM_DELETE_WINDOW", lambda: dismiss("done"))
    popup.bind("<Escape>", lambda _: dismiss("done"))
    popup.after(_SOUND_TIMEOUT * 1000, lambda: dismiss("done"))

    # 焦点
    popup.focus_force()
    popup.grab_set()
    parent.wait_window(popup)
    return result["value"]


def virtual_screen_geometry(root):
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
            w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
            h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def show_forced_rest_overlay(parent, seconds=FORCED_REST_SECONDS):
    """A reversible full-screen break blocker. Windows itself cannot auto-unlock."""
    stop_sound()
    overlay = tk.Toplevel(parent)
    overlay.overrideredirect(True)
    overlay.attributes("-topmost", True)
    overlay.configure(bg="#101418")
    overlay.protocol("WM_DELETE_WINDOW", lambda: None)

    x, y, w, h = virtual_screen_geometry(parent)
    overlay.geometry(f"{w}x{h}+{x}+{y}")
    overlay.update_idletasks()

    ui_scale = get_ui_scale(parent)
    scale = lambda value: round(value * ui_scale)
    canvas = tk.Canvas(
        overlay,
        width=w,
        height=h,
        bg="#101418",
        highlightthickness=0,
        bd=0,
    )
    canvas.pack(fill="both", expand=True)

    panel_w = min(scale(520), max(scale(340), w - scale(64)))
    panel_h = scale(250)
    panel_x = (w - panel_w) // 2
    panel_y = (h - panel_h) // 2
    canvas_image(
        canvas,
        panel_x,
        panel_y,
        render_rounded_rect(
            panel_w, panel_h, scale(28),
            "#FFFFFF", "#E7EAEE", 1, scale=ui_scale,
        ),
    )

    title_id = canvas.create_text(
        w // 2,
        panel_y + scale(48),
        text="强制休息",
        fill="#202124",
        font=("Microsoft YaHei UI", 18, "bold"),
    )
    message_id = canvas.create_text(
        w // 2,
        panel_y + scale(90),
        text="站起来活动 1 分钟，倒计时结束后自动恢复。",
        fill="#5F6368",
        font=("Microsoft YaHei UI", 10),
        width=panel_w - scale(64),
        justify="center",
    )
    timer_id = canvas.create_text(
        w // 2,
        panel_y + scale(158),
        text="01:00",
        fill="#202124",
        font=("Segoe UI", 34, "bold"),
    )
    canvas_image(
        canvas,
        panel_x + scale(46),
        panel_y + panel_h - scale(44),
        render_gradient_rect(panel_w - scale(92), scale(3),
                             ["#6D8FE6", "#A47ACF", "#D77D75", "#E5B35B", "#70A874"]),
    )

    def eat_event(_event=None):
        return "break"

    for seq in ("<Escape>", "<Alt-F4>", "<Control-KeyPress>", "<KeyPress>"):
        overlay.bind(seq, eat_event)
    overlay.bind_all("<ButtonPress>", eat_event)

    remaining = {"value": max(0, int(seconds))}

    def format_time(value):
        minutes, sec = divmod(max(0, value), 60)
        return f"{minutes:02d}:{sec:02d}"

    def finish():
        try:
            overlay.unbind_all("<ButtonPress>")
        except tk.TclError:
            pass
        try:
            overlay.grab_release()
        except tk.TclError:
            pass
        overlay.destroy()

    def tick():
        left = remaining["value"]
        canvas.itemconfigure(timer_id, text=format_time(left))
        if left <= 0:
            finish()
            return
        remaining["value"] = left - 1
        try:
            overlay.lift()
            overlay.focus_force()
        except tk.TclError:
            return
        overlay.after(1000, tick)

    try:
        overlay.grab_set_global()
    except tk.TclError:
        try:
            overlay.grab_set()
        except tk.TclError:
            pass
    overlay.focus_force()
    tick()
    parent.wait_window(overlay)


# ╔══════════════════════════════════════════════════════╗
# ║  桌面小人                                            ║
# ╚══════════════════════════════════════════════════════╝
def render_pet_frame(logical_w, logical_h, ui_scale, mode, frame, anger_level):
    from PIL import Image, ImageDraw

    high_scale = max(3, round(ui_scale * 3))
    hw, hh = round(logical_w * high_scale), round(logical_h * high_scale)
    img = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def s(value):
        return round(value * high_scale)

    def rounded(box, radius, fill, outline=None, width=1):
        d.rounded_rectangle(
            [s(box[0]), s(box[1]), s(box[2]), s(box[3])],
            radius=s(radius),
            fill=fill,
            outline=outline,
            width=max(1, s(width)) if outline else 1,
        )

    chair = "#D9E1EC"
    chair_dark = "#AEBBCC"
    shirt = "#6D8FE6"
    pants = "#475569"
    skin = "#FFD7B5"
    dark = "#273142"
    anger_t = min(1.0, max(0.0, anger_level / MAX_SNOOZE_COUNT))

    hot_face = mode in ("angry", "locked") or anger_level > 0
    if hot_face:
        skin = _blend_color("#FFD7B5", "#FF6B5F", anger_t)
        shirt = _blend_color("#6D8FE6", "#D77D75", min(1, anger_level / 5))

    if mode == "locked":
        d.polygon(
            [(s(69), s(63)), (s(92), s(32)), (s(116), s(61)),
             (s(146), s(31)), (s(159), s(68)), (s(194), s(65)),
             (s(169), s(93)), (s(188), s(123)), (s(149), s(116)),
             (s(123), s(148)), (s(103), s(116)), (s(66), s(127)),
             (s(85), s(95))],
            fill="#F97316",
        )
        d.polygon(
            [(s(86), s(75)), (s(106), s(50)), (s(122), s(74)),
             (s(145), s(49)), (s(153), s(79)), (s(181), s(78)),
             (s(158), s(101)), (s(172), s(126)), (s(143), s(117)),
             (s(121), s(139)), (s(104), s(117)), (s(76), s(130)),
             (s(90), s(102))],
            fill="#111827",
        )

    # Chair
    rounded((73, 86, 158, 136), 16, chair)
    rounded((60, 128, 176, 151), 13, chair_dark)
    rounded((72, 146, 88, 177), 8, chair_dark)
    rounded((148, 146, 164, 177), 8, chair_dark)

    if mode == "stretch":
        # Standing stretch
        d.ellipse([s(102), s(42), s(134), s(74)], fill=skin)
        d.arc([s(100), s(40), s(136), s(77)], 205, 335, fill=dark, width=s(4))
        if anger_level:
            d.line([s(109), s(56), s(116), s(53)], fill="#7F1D1D", width=s(3))
            d.line([s(120), s(53), s(127), s(56)], fill="#7F1D1D", width=s(3))
            d.ellipse([s(110), s(59), s(114), s(63)], fill="#111827")
            d.ellipse([s(122), s(59), s(126), s(63)], fill="#111827")
            d.arc([s(110), s(64), s(126), s(77)], 200, 340,
                  fill="#7F1D1D", width=s(2))
            for i in range(max(1, anger_level)):
                steam_x = 97 + i * 10
                d.line([s(steam_x), s(42), s(steam_x + 4), s(34)],
                       fill="#EF4444", width=s(2))
        else:
            d.ellipse([s(110), s(58), s(113), s(61)], fill=dark)
            d.ellipse([s(123), s(58), s(126), s(61)], fill=dark)
        rounded((105, 77, 132, 125), 11, shirt)
        d.line([s(109), s(87), s(78), s(50)], fill=shirt, width=s(8))
        d.line([s(128), s(87), s(161), s(50)], fill=shirt, width=s(8))
        d.ellipse([s(71), s(45), s(83), s(57)], fill=skin)
        d.ellipse([s(156), s(45), s(168), s(57)], fill=skin)
        d.line([s(112), s(123), s(94), s(169)], fill=pants, width=s(10))
        d.line([s(126), s(123), s(149), s(169)], fill=pants, width=s(10))
        d.line([s(86), s(171), s(103), s(171)], fill=pants, width=s(8))
        d.line([s(143), s(171), s(160), s(171)], fill=pants, width=s(8))
    else:
        lean = 0 if mode == "upright" else math.sin(frame / 2) * 2
        head_x = 116 + lean
        if hot_face:
            aura = _blend_color("#FFE7DF", "#FF5A4F", anger_t)
            d.ellipse([s(head_x - 24), s(35), s(head_x + 24), s(83)], fill=aura)
        d.ellipse([s(head_x - 17), s(43), s(head_x + 17), s(77)], fill=skin)
        d.arc([s(head_x - 18), s(40), s(head_x + 18), s(78)],
              205, 335, fill=dark, width=s(4))

        if mode in ("angry", "locked"):
            brow = "#7F1D1D" if mode == "angry" else "#FFFFFF"
            d.line([s(head_x - 9), s(57), s(head_x - 2), s(54)],
                   fill=brow, width=s(3))
            d.line([s(head_x + 2), s(54), s(head_x + 9), s(57)],
                   fill=brow, width=s(3))
            eye = "#111827" if mode == "angry" else "#EF4444"
            d.ellipse([s(head_x - 8), s(60), s(head_x - 4), s(64)], fill=eye)
            d.ellipse([s(head_x + 4), s(60), s(head_x + 8), s(64)], fill=eye)
            d.arc([s(head_x - 8), s(65), s(head_x + 8), s(78)],
                  200, 340, fill="#7F1D1D", width=s(2))
            for i in range(max(1, anger_level)):
                steam_x = head_x - 22 + i * 11
                steam_y = 31 - (i % 2) * 4
                d.line([s(steam_x), s(steam_y + 8),
                        s(steam_x + 4), s(steam_y)],
                       fill="#EF4444", width=s(2))
        else:
            d.ellipse([s(head_x - 7), s(58), s(head_x - 4), s(61)], fill=dark)
            d.ellipse([s(head_x + 5), s(58), s(head_x + 8), s(61)], fill=dark)

        body_left = 99 + lean / 2
        body_right = 135 + lean / 2
        rounded((body_left, 78, body_right, 122), 12, shirt)
        d.line([s(body_left + 5), s(87), s(78), s(111)], fill=shirt, width=s(7))
        d.line([s(body_right - 5), s(87), s(158), s(111)], fill=shirt, width=s(7))

        if mode == "upright":
            d.line([s(106), s(122), s(92), s(164)], fill=pants, width=s(10))
            d.line([s(128), s(122), s(145), s(164)], fill=pants, width=s(10))
            d.line([s(85), s(166), s(102), s(166)], fill=pants, width=s(8))
            d.line([s(138), s(166), s(156), s(166)], fill=pants, width=s(8))
        else:
            cross = frame % 6 < 3
            if cross:
                d.line([s(106), s(123), s(147), s(160)], fill=pants, width=s(10))
                d.line([s(128), s(123), s(91), s(163)], fill=pants, width=s(10))
                d.line([s(143), s(162), s(162), s(156)], fill=pants, width=s(8))
                d.line([s(82), s(165), s(100), s(169)], fill=pants, width=s(8))
            else:
                d.line([s(106), s(123), s(96), s(164)], fill=pants, width=s(10))
                d.line([s(128), s(123), s(145), s(164)], fill=pants, width=s(10))
                d.line([s(89), s(166), s(106), s(166)], fill=pants, width=s(8))
                d.line([s(138), s(166), s(156), s(166)], fill=pants, width=s(8))

    if hot_face:
        marks = min(5, max(1, anger_level))
        for i in range(marks):
            x = 157 + i * 8
            y = 47 + (i % 2) * 10
            color = "#D77D75" if mode == "angry" else "#111827"
            d.line([s(x), s(y + 9), s(x + 5), s(y)], fill=color, width=s(3))

    if mode == "upright":
        d.line([s(116), s(77), s(116), s(122)], fill="#FFFFFF", width=s(3))

    return img.resize(
        (round(logical_w * ui_scale), round(logical_h * ui_scale)),
        Image.Resampling.LANCZOS,
    )


def _render_pet_frame_3d_legacy_unused(logical_w, logical_h, ui_scale, mode, frame, anger_level):
    from PIL import Image, ImageDraw

    high_scale = max(3, round(ui_scale * 3))
    hw, hh = round(logical_w * high_scale), round(logical_h * high_scale)
    img = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def s(value):
        return round(value * high_scale)

    def ellipse(box, fill, outline=None, width=1):
        d.ellipse(
            [s(box[0]), s(box[1]), s(box[2]), s(box[3])],
            fill=fill,
            outline=outline,
            width=max(1, s(width)) if outline else 1,
        )

    def rounded(box, radius, fill, outline=None, width=1):
        d.rounded_rectangle(
            [s(box[0]), s(box[1]), s(box[2]), s(box[3])],
            radius=s(radius),
            fill=fill,
            outline=outline,
            width=max(1, s(width)) if outline else 1,
        )

    def capsule(p1, p2, width, fill, shadow=True):
        x1, y1 = p1
        x2, y2 = p2
        r = width / 2
        if shadow:
            shadow_color = "#B9C2CD"
            d.line([s(x1 + 2), s(y1 + 3), s(x2 + 2), s(y2 + 3)],
                   fill=shadow_color, width=s(width))
            ellipse((x1 - r + 2, y1 - r + 3, x1 + r + 2, y1 + r + 3), shadow_color)
            ellipse((x2 - r + 2, y2 - r + 3, x2 + r + 2, y2 + r + 3), shadow_color)
        d.line([s(x1), s(y1), s(x2), s(y2)], fill=fill, width=s(width))
        ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill)
        ellipse((x2 - r, y2 - r, x2 + r, y2 + r), fill)
        if width >= 11:
            d.line([s(x1 - 1), s(y1 - 1), s(x2 - 1), s(y2 - 1)],
                   fill="#FFFFFF", width=s(max(2, width * 0.22)))

    body = "#EEF2F6"
    limb = "#D9E1EA"
    chair = "#DCE5F0"
    chair_dark = "#AEBBCC"
    dark = "#273142"
    anger_t = min(1.0, max(0.0, anger_level / MAX_SNOOZE_COUNT))
    hot_face = mode in ("angry", "locked") or anger_level > 0

    def draw_face(cx, cy, r):
        if hot_face:
            ellipse((cx - r - 7, cy - r - 7, cx + r + 7, cy + r + 7),
                    _blend_color("#FFE2DA", "#FF4F44", anger_t * 0.9))
        head = _blend_color("#F4F6F8", "#FF7368", anger_t if hot_face else 0)
        ellipse((cx - r, cy - r, cx + r, cy + r), head, "#C8D0DA", 1)
        ellipse((cx - r + 4, cy - r + 4, cx + r - 7, cy - r + 15), "#FFFFFF")

        # 侧脸：朝左，看得出是侧面。
        d.polygon(
            [(s(cx - r), s(cy - 1)), (s(cx - r - 7), s(cy + 3)), (s(cx - r), s(cy + 7))],
            fill=head,
            outline="#C8D0DA",
        )
        if hot_face:
            d.line([s(cx - 9), s(cy - 1), s(cx - 2), s(cy - 5)],
                   fill="#7F1D1D", width=s(3))
            ellipse((cx - 7, cy + 3, cx - 3, cy + 7), "#111827")
            d.arc([s(cx - 9), s(cy + 9), s(cx + 5), s(cy + 21)],
                  205, 335, fill="#7F1D1D", width=s(2))
            for i in range(max(1, anger_level)):
                x = cx - 18 + i * 9
                y = cy - r - 10 - (i % 2) * 4
                d.line([s(x), s(y + 8), s(x + 4), s(y)],
                       fill="#EF4444", width=s(2))
        else:
            ellipse((cx - 7, cy + 2, cx - 4, cy + 5), dark)
            d.arc([s(cx - 8), s(cy + 7), s(cx + 4), s(cy + 16)],
                  20, 145, fill="#64748B", width=s(2))

    def draw_chair():
        d.ellipse([s(43), s(137), s(159), s(151)], fill=(0, 0, 0, 24))
        d.polygon(
            [(s(112), s(55)), (s(142), s(65)), (s(142), s(120)), (s(112), s(112))],
            fill=chair,
        )
        d.polygon(
            [(s(55), s(105)), (s(125), s(105)), (s(146), s(119)), (s(76), s(120))],
            fill="#E5EBF3",
        )
        d.polygon(
            [(s(76), s(120)), (s(146), s(119)), (s(139), s(132)), (s(69), s(133))],
            fill=chair_dark,
        )
        capsule((76, 130), (67, 151), 7, chair_dark, False)
        capsule((130, 129), (145, 151), 7, chair_dark, False)

    if mode == "locked":
        d.polygon(
            [(s(41), s(56)), (s(70), s(24)), (s(93), s(50)), (s(122), s(22)),
             (s(136), s(58)), (s(169), s(57)), (s(145), s(85)), (s(164), s(116)),
             (s(128), s(107)), (s(101), s(137)), (s(83), s(108)), (s(48), s(120)),
             (s(64), s(87))],
            fill="#F97316",
        )

    draw_chair()

    if mode == "stretch":
        # 起身：人在椅子前侧，身体完全竖起来，手臂伸展。
        capsule((87, 62), (94, 102), 22, body)
        capsule((86, 71), (58, 36), 8, body)
        capsule((96, 71), (126, 36), 8, body)
        ellipse((52, 30, 66, 44), body)
        ellipse((121, 30, 135, 44), body)
        capsule((90, 101), (74, 141), 10, limb)
        capsule((97, 101), (118, 141), 10, limb)
        capsule((67, 143), (84, 143), 7, limb, False)
        capsule((113, 143), (130, 143), 7, limb, False)
        draw_face(89, 40, 16)
    else:
        lean = 0 if mode == "upright" else math.sin(frame / 2) * 1.7
        capsule((91 + lean, 65), (99 + lean, 102), 22, body)
        capsule((89 + lean, 75), (71, 95), 7, body)
        capsule((99 + lean, 75), (121, 96), 7, body)

        if mode == "upright":
            capsule((98, 103), (73, 114), 11, limb)
            capsule((73, 114), (71, 145), 10, limb)
            capsule((105, 104), (129, 115), 11, limb)
            capsule((129, 115), (129, 145), 10, limb)
            capsule((62, 147), (80, 147), 7, limb, False)
            capsule((122, 147), (140, 147), 7, limb, False)
            d.line([s(97), s(66), s(100), s(102)], fill="#FFFFFF", width=s(3))
        else:
            cross = frame % 6 < 3
            if cross:
                capsule((98, 103), (128, 114), 11, limb)
                capsule((128, 114), (144, 103), 10, limb)
                capsule((105, 104), (74, 114), 11, limb)
                capsule((74, 114), (71, 145), 10, limb)
                capsule((137, 102), (154, 102), 7, limb, False)
                capsule((62, 147), (80, 147), 7, limb, False)
            else:
                capsule((98, 103), (73, 114), 11, limb)
                capsule((73, 114), (71, 145), 10, limb)
                capsule((105, 104), (129, 115), 11, limb)
                capsule((129, 115), (129, 145), 10, limb)
                capsule((62, 147), (80, 147), 7, limb, False)
                capsule((122, 147), (140, 147), 7, limb, False)
        draw_face(84 + lean, 45, 17)

    return img.resize(
        (round(logical_w * ui_scale), round(logical_h * ui_scale)),
        Image.Resampling.LANCZOS,
    )


def render_pet_frame_3d(logical_w, logical_h, ui_scale, mode, frame, anger_level):
    from PIL import Image, ImageDraw

    high_scale = max(3, round(ui_scale * 3))
    hw, hh = round(logical_w * high_scale), round(logical_h * high_scale)
    key = (255, 0, 254, 0)
    img = Image.new("RGBA", (hw, hh), key)
    d = ImageDraw.Draw(img)

    def s(value):
        return round(value * high_scale)

    def ellipse(box, fill, outline=None, width=1):
        d.ellipse(
            [s(box[0]), s(box[1]), s(box[2]), s(box[3])],
            fill=fill,
            outline=outline,
            width=max(1, s(width)) if outline else 1,
        )

    def rounded(box, radius, fill, outline=None, width=1):
        d.rounded_rectangle(
            [s(box[0]), s(box[1]), s(box[2]), s(box[3])],
            radius=s(radius),
            fill=fill,
            outline=outline,
            width=max(1, s(width)) if outline else 1,
        )

    def limb_line(points, width, fill):
        d.line([(s(x), s(y)) for x, y in points], fill=fill, width=s(width), joint="curve")
        r = width / 2
        for x, y in (points[0], points[-1]):
            ellipse((x - r, y - r, x + r, y + r), fill)

    white = "#F4F6F8"
    shade = "#D8DEE8"
    edge = "#BCC6D2"
    dark = "#273142"
    chair = "#2F3542"
    chair_edge = "#111827"
    anger_t = min(1.0, max(0.0, anger_level / MAX_SNOOZE_COUNT))
    hot = mode in ("angry", "locked") or anger_level > 0

    def draw_head(cx, cy, r):
        head = _blend_color(white, "#FF7168", anger_t if hot else 0)
        if hot:
            ellipse((cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6),
                    _blend_color("#FFE1DA", "#FF5248", anger_t * 0.9))
        ellipse((cx - r, cy - r, cx + r, cy + r), head, edge, 1)
        ellipse((cx - r + 4, cy - r + 4, cx + r - 7, cy - r + 14), "#FFFFFF")
        # nose, side-view cue
        d.polygon(
            [(s(cx - r + 1), s(cy)), (s(cx - r - 5), s(cy + 4)), (s(cx - r + 1), s(cy + 8))],
            fill=head,
            outline=edge,
        )
        if hot:
            d.line([s(cx - 9), s(cy - 1), s(cx - 2), s(cy - 5)],
                   fill="#7F1D1D", width=s(2))
            ellipse((cx - 8, cy + 3, cx - 4, cy + 7), dark)
            d.arc([s(cx - 8), s(cy + 9), s(cx + 4), s(cy + 20)],
                  205, 335, fill="#7F1D1D", width=s(2))
            for i in range(max(1, anger_level)):
                x = cx - 16 + i * 8
                y = cy - r - 10 - (i % 2) * 3
                d.line([s(x), s(y + 7), s(x + 4), s(y)], fill="#EF4444", width=s(2))
        else:
            ellipse((cx - 8, cy + 3, cx - 5, cy + 6), dark)
            d.arc([s(cx - 8), s(cy + 7), s(cx + 4), s(cy + 16)],
                  20, 150, fill="#64748B", width=s(2))

    def draw_chair():
        d.ellipse([s(36), s(126), s(148), s(139)], fill=(0, 0, 0, 30))
        # Backrest sits behind the person, matching the reference-style office chair.
        rounded((88, 52, 121, 112), 10, chair, chair_edge, 1)
        rounded((48, 92, 116, 117), 12, "#343B49", chair_edge, 1)
        rounded((48, 92, 116, 104), 8, "#4B5565")
        limb_line([(54, 103), (39, 109)], 6, chair)
        limb_line([(110, 102), (126, 108)], 6, chair)
        limb_line([(82, 116), (82, 134)], 7, chair_edge)
        d.line([s(82), s(134), s(51), s(142)], fill=chair_edge, width=s(4))
        d.line([s(82), s(134), s(113), s(142)], fill=chair_edge, width=s(4))
        d.line([s(82), s(134), s(82), s(145)], fill=chair_edge, width=s(4))
        ellipse((47, 139, 56, 148), chair_edge)
        ellipse((109, 139, 118, 148), chair_edge)
        ellipse((78, 143, 87, 152), chair_edge)

    def draw_seated():
        pose = "upright"
        lean = 0
        if mode == "upright":
            pose = "upright"
        elif mode in ("angry", "locked"):
            pose = "slouch"
            lean = 1.8
        else:
            pose_cycle = ["slouch", "lean_left", "lean_right", "cross_leg", "desk_slump"]
            pose = pose_cycle[(frame // 6) % len(pose_cycle)]
            lean_map = {
                "slouch": 1.8,
                "lean_left": -3.0,
                "lean_right": 3.0,
                "cross_leg": math.sin(frame / 2) * 1.2,
                "desk_slump": -1.5,
            }
            lean = lean_map[pose]

        draw_chair()
        head_y = 43
        body_top_y = 61
        body_bottom_y = 93
        if pose == "slouch":
            head_y = 50
            body_top_y = 68
            body_bottom_y = 98
        elif pose == "desk_slump":
            head_y = 55
            body_top_y = 72
            body_bottom_y = 101

        draw_head(72 + lean, head_y, 15)
        rounded((72 + lean, body_top_y, 96 + lean, body_bottom_y), 10, white, edge, 1)
        d.line([s(83 + lean), s(body_top_y + 1), s(87 + lean), s(body_bottom_y - 1)],
               fill="#FFFFFF", width=s(2))

        if pose == "desk_slump":
            limb_line([(75 + lean, 79), (53, 105)], 7, white)
            limb_line([(93 + lean, 79), (120, 101)], 7, white)
        elif pose == "lean_left":
            limb_line([(74 + lean, 72), (50, 85)], 7, white)
            limb_line([(93 + lean, 73), (113, 96)], 7, white)
        elif pose == "lean_right":
            limb_line([(75 + lean, 73), (57, 98)], 7, white)
            limb_line([(93 + lean, 72), (120, 88)], 7, white)
        else:
            limb_line([(75 + lean, 72), (55, 91)], 7, white)
            limb_line([(92 + lean, 73), (108, 91)], 7, white)

        if mode == "upright":
            # normal seated posture: thigh horizontal, calf vertical
            limb_line([(90, 94), (63, 104)], 10, shade)
            limb_line([(63, 104), (60, 134)], 9, shade)
            limb_line([(99, 96), (119, 106)], 10, shade)
            limb_line([(119, 106), (119, 134)], 9, shade)
            d.line([s(84), s(62), s(92), s(94)], fill="#FFFFFF", width=s(3))
        elif pose == "cross_leg":
            # one leg over the other, but kept readable and not tangled
            limb_line([(90, 94), (119, 103)], 10, shade)
            limb_line([(119, 103), (143, 98)], 9, shade)
            limb_line([(99, 96), (67, 106)], 10, shade)
            limb_line([(67, 106), (58, 134)], 9, shade)
            limb_line([(136, 98), (154, 98)], 6, shade)
            limb_line([(50, 136), (68, 136)], 6, shade)
        elif pose == "desk_slump":
            limb_line([(90, 98), (60, 107)], 10, shade)
            limb_line([(60, 107), (55, 134)], 9, shade)
            limb_line([(99, 99), (128, 108)], 10, shade)
            limb_line([(128, 108), (136, 129)], 9, shade)
            limb_line([(48, 136), (66, 136)], 6, shade)
            limb_line([(129, 130), (146, 130)], 6, shade)
        else:
            # relaxed leg: one leg slightly forward, no crossed knot.
            limb_line([(90, 94), (61, 104)], 10, shade)
            limb_line([(61, 104), (56, 134)], 9, shade)
            limb_line([(99, 96), (128, 102)], 10, shade)
            limb_line([(128, 102), (136, 126)], 9, shade)
            limb_line([(49, 136), (67, 136)], 6, shade)
            limb_line([(112, 136), (137, 136)], 6, shade)

    def draw_standing():
        draw_chair()
        # standing next to chair, arms gently up, side-view still clear
        draw_head(76, 35, 15)
        rounded((73, 55, 96, 92), 10, white, edge, 1)
        limb_line([(75, 64), (52, 43)], 7, white)
        limb_line([(94, 64), (119, 43)], 7, white)
        ellipse((47, 37, 59, 49), white, edge, 1)
        ellipse((114, 37, 126, 49), white, edge, 1)
        limb_line([(80, 92), (66, 133)], 9, shade)
        limb_line([(91, 92), (108, 133)], 9, shade)
        limb_line([(58, 135), (75, 135)], 6, shade)
        limb_line([(101, 135), (119, 135)], 6, shade)

    if mode == "locked":
        d.polygon(
            [(s(35), s(49)), (s(63), s(22)), (s(82), s(45)), (s(110), s(20)),
             (s(121), s(51)), (s(150), s(50)), (s(130), s(76)), (s(146), s(104)),
             (s(115), s(95)), (s(92), s(122)), (s(77), s(96)), (s(43), s(108)),
             (s(58), s(78))],
            fill="#F97316",
        )

    if mode == "stretch":
        draw_standing()
    else:
        draw_seated()

    out = img.resize(
        (round(logical_w * ui_scale), round(logical_h * ui_scale)),
        Image.Resampling.LANCZOS,
    )

    # Tk transparent-color windows show colored fringes with semi-transparent pixels.
    # Force all non-empty pixels opaque so the desktop pet has no pink outline.
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = pixels[x, y]
            if a < 18:
                pixels[x, y] = (255, 0, 254, 0)
            elif a < 255:
                pixels[x, y] = (r, g, b, 255)
    return out


def render_pet_frame_3d(logical_w, logical_h, ui_scale, mode, frame, anger_level):
    from PIL import Image, ImageDraw

    high_scale = max(4, round(ui_scale * 4))
    hw, hh = round(logical_w * high_scale), round(logical_h * high_scale)
    key = (255, 0, 254, 0)
    img = Image.new("RGBA", (hw, hh), key)
    d = ImageDraw.Draw(img)

    def s(value):
        return round(value * high_scale)

    def ellipse(box, fill, outline=None, width=1):
        d.ellipse(
            [s(box[0]), s(box[1]), s(box[2]), s(box[3])],
            fill=fill,
            outline=outline,
            width=max(1, s(width)) if outline else 1,
        )

    def rounded(box, radius, fill, outline=None, width=1):
        d.rounded_rectangle(
            [s(box[0]), s(box[1]), s(box[2]), s(box[3])],
            radius=s(radius),
            fill=fill,
            outline=outline,
            width=max(1, s(width)) if outline else 1,
        )

    def soft_line(points, width, fill, highlight=True):
        xy = [(s(x), s(y)) for x, y in points]
        d.line(xy, fill=fill, width=s(width), joint="curve")
        r = width / 2
        for x, y in (points[0], points[-1]):
            ellipse((x - r, y - r, x + r, y + r), fill)
        if highlight and width >= 10:
            offset = max(1, width * 0.16)
            hi = "#FFFFFF"
            hi_xy = [(s(x - offset), s(y - offset)) for x, y in points]
            d.line(hi_xy, fill=hi, width=max(1, s(width * 0.18)), joint="curve")

    white = "#F2F5F8"
    light = "#FFFFFF"
    shade = "#D9E0EA"
    edge = "#B9C3D0"
    dark = "#273142"
    chair = "#252B36"
    chair_mid = "#343C49"
    chair_hi = "#4B5565"
    chair_edge = "#111827"
    anger_t = min(1.0, max(0.0, anger_level / MAX_SNOOZE_COUNT))
    hot = mode in ("angry", "locked") or anger_level > 0

    def draw_office_chair():
        d.ellipse([s(34), s(126), s(151), s(140)], fill=(0, 0, 0, 32))
        rounded((94, 50, 126, 113), 12, chair, chair_edge, 1)
        rounded((98, 55, 121, 103), 9, chair_mid)
        rounded((54, 91, 115, 115), 12, chair, chair_edge, 1)
        rounded((57, 92, 112, 103), 8, chair_hi)
        soft_line([(56, 101), (40, 108)], 7, chair, False)
        soft_line([(108, 101), (125, 108)], 7, chair, False)
        soft_line([(82, 113), (82, 134)], 7, chair_edge, False)
        d.line([s(82), s(134), s(51), s(142)], fill=chair_edge, width=s(4))
        d.line([s(82), s(134), s(113), s(142)], fill=chair_edge, width=s(4))
        d.line([s(82), s(134), s(82), s(145)], fill=chair_edge, width=s(4))
        ellipse((47, 139, 56, 148), chair_edge)
        ellipse((109, 139, 118, 148), chair_edge)
        ellipse((78, 143, 87, 152), chair_edge)

    def draw_head(cx, cy, r):
        head = _blend_color(white, "#FF7168", anger_t if hot else 0)
        if hot:
            ellipse((cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6),
                    _blend_color("#FFE2DC", "#FF5148", anger_t * 0.95))
        ellipse((cx - r + 1, cy - r + 3, cx + r + 1, cy + r + 3), "#CBD3DE")
        ellipse((cx - r, cy - r, cx + r, cy + r), head, edge, 1)
        ellipse((cx - r + 5, cy - r + 5, cx + r - 8, cy - r + 15), light)
        if hot:
            d.line([s(cx - 10), s(cy - 1), s(cx - 3), s(cy - 5)],
                   fill="#7F1D1D", width=s(2))
            ellipse((cx - 8, cy + 2, cx - 5, cy + 5), dark)
            d.arc([s(cx - 8), s(cy + 9), s(cx + 5), s(cy + 20)],
                  205, 335, fill="#7F1D1D", width=s(2))
            for i in range(max(1, anger_level)):
                x = cx - 16 + i * 8
                y = cy - r - 10 - (i % 2) * 3
                d.line([s(x), s(y + 7), s(x + 4), s(y)], fill="#EF4444", width=s(2))
        else:
            ellipse((cx - 8, cy + 2, cx - 5, cy + 5), dark)
            d.arc([s(cx - 9), s(cy + 7), s(cx + 4), s(cy + 16)],
                  20, 150, fill="#64748B", width=s(2))

    def body_capsule(top, bottom, width, fill=white):
        soft_line([top, bottom], width, fill)
        ellipse((top[0] - width * 0.42, top[1] - width * 0.28,
                 top[0] + width * 0.42, top[1] + width * 0.28), fill)

    def draw_seated():
        if mode == "upright":
            pose = "upright"
            lean = 0
        elif mode in ("angry", "locked"):
            pose = "slouch"
            lean = 1.5
        else:
            poses = ["slouch", "lean_left", "lean_right", "cross_leg", "desk_slump"]
            pose = poses[(frame // 7) % len(poses)]
            lean = {
                "slouch": 1.5,
                "lean_left": -3.0,
                "lean_right": 3.0,
                "cross_leg": math.sin(frame / 2) * 1.2,
                "desk_slump": -1.5,
            }[pose]

        draw_office_chair()

        head = (64 + lean, 38)
        shoulder = (78 + lean, 61)
        hip = (88 + lean, 94)
        if pose == "slouch":
            head = (62 + lean, 48)
            shoulder = (75 + lean, 70)
            hip = (90 + lean, 98)
        elif pose == "desk_slump":
            head = (59 + lean, 53)
            shoulder = (72 + lean, 73)
            hip = (90 + lean, 101)
        elif pose == "lean_left":
            head = (60 + lean, 43)
            shoulder = (75 + lean, 64)
        elif pose == "lean_right":
            head = (68 + lean, 41)
            shoulder = (82 + lean, 63)

        # Legs first, so the rounded torso/head clearly sit in front of them.
        if pose == "cross_leg":
            soft_line([(86, 94), (59, 104)], 14, shade)
            soft_line([(59, 104), (50, 130)], 12, shade)
            soft_line([(95, 96), (120, 105)], 14, shade)
            soft_line([(120, 105), (139, 99)], 12, shade)
            ellipse((43, 128, 64, 137), shade, edge, 1)
            ellipse((132, 96, 153, 105), shade, edge, 1)
        elif pose == "desk_slump":
            soft_line([(86, 98), (57, 107)], 14, shade)
            soft_line([(57, 107), (51, 130)], 12, shade)
            soft_line([(96, 99), (125, 108)], 14, shade)
            soft_line([(125, 108), (133, 128)], 12, shade)
            ellipse((44, 128, 65, 137), shade, edge, 1)
            ellipse((126, 126, 146, 135), shade, edge, 1)
        else:
            soft_line([(86, 94), (58, 104)], 14, shade)
            soft_line([(58, 104), (53, 130)], 12, shade)
            soft_line([(96, 96), (119, 106)], 14, shade)
            soft_line([(119, 106), (118, 130)], 12, shade)
            ellipse((45, 128, 66, 137), shade, edge, 1)
            ellipse((109, 128, 130, 137), shade, edge, 1)

        body_capsule(shoulder, hip, 26, white)
        if pose == "desk_slump":
            soft_line([(75 + lean, 78), (50, 101)], 10, white)
            soft_line([(91 + lean, 79), (118, 101)], 10, white)
        elif pose == "lean_left":
            soft_line([(74 + lean, 72), (50, 86)], 10, white)
            soft_line([(92 + lean, 72), (112, 95)], 10, white)
        elif pose == "lean_right":
            soft_line([(76 + lean, 72), (58, 96)], 10, white)
            soft_line([(93 + lean, 72), (120, 88)], 10, white)
        else:
            soft_line([(75 + lean, 71), (55, 91)], 10, white)
            soft_line([(93 + lean, 72), (111, 91)], 10, white)
        draw_head(head[0], head[1], 17)

        if mode == "upright":
            d.line([s(82), s(63), s(89), s(93)], fill=light, width=s(3))

    def draw_standing():
        draw_office_chair()
        draw_head(72, 35, 17)
        body_capsule((78, 57), (86, 94), 25, white)
        soft_line([(77, 65), (52, 42)], 10, white)
        soft_line([(92, 65), (118, 42)], 10, white)
        ellipse((46, 36, 59, 49), white, edge, 1)
        ellipse((112, 36, 125, 49), white, edge, 1)
        soft_line([(81, 94), (66, 132)], 12, shade)
        soft_line([(91, 94), (108, 132)], 12, shade)
        ellipse((58, 130, 77, 139), shade, edge, 1)
        ellipse((101, 130, 121, 139), shade, edge, 1)

    if mode == "locked":
        d.polygon(
            [(s(35), s(49)), (s(63), s(22)), (s(82), s(45)), (s(110), s(20)),
             (s(121), s(51)), (s(150), s(50)), (s(130), s(76)), (s(146), s(104)),
             (s(115), s(95)), (s(92), s(122)), (s(77), s(96)), (s(43), s(108)),
             (s(58), s(78))],
            fill="#F97316",
        )

    if mode == "stretch":
        draw_standing()
    else:
        draw_seated()

    out = img.resize(
        (round(logical_w * ui_scale), round(logical_h * ui_scale)),
        Image.Resampling.LANCZOS,
    )
    pixels = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = pixels[x, y]
            if a < 18:
                pixels[x, y] = (255, 0, 254, 0)
            elif a < 255:
                pixels[x, y] = (r, g, b, 255)
    return out


class DesktopPet:
    # [CLAUDE] 交叉淡入淡出：帧数和状态缓存
    TRANSITION_FRAMES = 5

    TRANSITION_FRAMES = 5
    HEAD_ANCHORS = {
        "sit_lean_bad": (206, 98, 66),
        "sit_relaxed": (239, 92, 61),
        "sit_cross_leg": (226, 92, 60),
        "sit_upright": (176, 93, 61),
        "stand_getting_up": (113, 110, 78),
        "stand_stretch_up": (174, 145, 80),
        "stand_neck": (211, 123, 84),
    }

    def __init__(self, root):
        self.root = root
        self.ui_scale = get_ui_scale(root) * 0.72
        self.logical_w = 160
        self.logical_h = 145
        self.w = round(self.logical_w * self.ui_scale)
        self.h = round(self.logical_h * self.ui_scale)
        self.mode = "idle"
        self.frame = 0
        self.anger_level = 0
        self.message = ""
        self.message_until = 0
        self.sprite_cache = {}
        self.auto_revert_at = 0
        self.transparent = "#02040A"
        self.hint_surface = "#FFFFFF"
        self.hint_w = self._scale(122)
        self.hint_h = self._scale(30)
        # [CLAUDE] 交叉淡入淡出状态
        self._last_sprite_name = None
        self._last_sprite_img = None
        self._transition_left = 0
        self._qt_hint_process = None
        self._drag_offset = (0, 0)
        self._press_root = (0, 0)
        self._drag_moved = False
        self._last_poke_at = 0
        self._visible_bbox = (0, 0, self.w, self.h)
        self._qt_hint_anchor_path = None
        self._qt_hint_until = 0
        self._popup_anchor_local = None
        self._pet_image_item = None
        self._pet_photo = None
        self._fullscreen_hits = 0
        self._hidden_for_fullscreen = False

        self.window = tk.Toplevel(root)
        self.window.title("站立提醒桌宠")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", False)  # [CLAUDE] 不置顶，被应用窗口覆盖
        self.window.configure(bg=self.transparent)
        try:
            self.window.attributes("-transparentcolor", self.transparent)
        except tk.TclError:
            pass
        self.window._pet_anchor_provider = self._qt_anchor

        sw, sh = self.window.winfo_screenwidth(), self.window.winfo_screenheight()
        self.window.geometry(f"{self.w}x{self.h}+{sw - self.w - 36}+{sh - self.h - 96}")

        self.canvas = tk.Canvas(
            self.window,
            width=self.w,
            height=self.h,
            bg=self.transparent,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self._pet_image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._start_move)
        self.canvas.bind("<B1-Motion>", self._do_move)
        self.canvas.bind("<ButtonRelease-1>", self._end_click)
        self.canvas.bind("<Double-Button-1>", self._double_click)

        self.hint_window = tk.Toplevel(root)
        self.hint_window.overrideredirect(True)
        self.hint_window.attributes("-topmost", False)  # [CLAUDE] 跟随桌宠，不独立置顶
        self.hint_window.configure(bg=self.hint_surface)

        self.hint_canvas = tk.Canvas(
            self.hint_window,
            width=self.hint_w,
            height=self.hint_h,
            bg=self.hint_surface,
            highlightthickness=0,
            bd=0,
        )
        self.hint_canvas.pack(fill="both", expand=True)
        apply_rounded_window_region(
            self.hint_window,
            self.hint_w,
            self.hint_h,
            self._scale(14),
        )
        self.hint_window.withdraw()

        self._animate()

    def _scale(self, value):
        return round(value * self.ui_scale)

    def _qt_anchor(self, _kind="popup"):
        self.window.update_idletasks()
        if _kind == "popup" and self._popup_anchor_local:
            center_x, anchor_y = self._popup_anchor_local
        else:
            center_x, head_top_y = self._current_head_anchor()
            if _kind == "hint":
                anchor_y = head_top_y - self._scale(4)
            else:
                anchor_y = head_top_y - self._scale(8)
        return (
            self.window.winfo_x() + center_x,
            self.window.winfo_y() + anchor_y,
        )

    def _current_head_anchor(self):
        return self._head_anchor_for_sprite(self._sprite_name())

    def _head_anchor_for_sprite(self, sprite_name):
        try:
            sprite = self._load_sprite(sprite_name, 0)
            scale = min(self.w / sprite.width, self.h / sprite.height)
            scaled_w = max(1, round(sprite.width * scale))
            scaled_h = max(1, round(sprite.height * scale))
            offset_x = (self.w - scaled_w) // 2
            offset_y = self.h - scaled_h
            ax, ay, ar = self.HEAD_ANCHORS.get(sprite_name, self.HEAD_ANCHORS["sit_relaxed"])
            return (
                offset_x + ax * scale,
                offset_y + (ay - ar) * scale,
            )
        except Exception:
            x1, y1, x2, _y2 = self._current_visible_bbox()
            return x1 + (x2 - x1) / 2, y1

    def _stable_stand_popup_anchor(self):
        anchors = []
        for sprite_name in ("stand_getting_up", "stand_stretch_up", "stand_neck"):
            try:
                anchors.append(self._head_anchor_for_sprite(sprite_name))
            except Exception:
                pass
        if not anchors:
            center_x, head_top_y = self._current_head_anchor()
            return center_x, head_top_y - self._scale(8)
        center_x = sum(x for x, _y in anchors) / len(anchors)
        head_top_y = min(y for _x, y in anchors)
        return center_x, head_top_y - self._scale(8)

    def _current_visible_bbox(self):
        try:
            sprite = self._load_sprite(self._sprite_name(), 0)
            bbox = sprite.getbbox()
            if bbox:
                scale = min(self.w / sprite.width, self.h / sprite.height)
                scaled_w = max(1, round(sprite.width * scale))
                scaled_h = max(1, round(sprite.height * scale))
                x = (self.w - scaled_w) // 2
                y = self.h - scaled_h
                return (
                    x + bbox[0] * scale,
                    y + bbox[1] * scale,
                    x + bbox[2] * scale,
                    y + bbox[3] * scale,
                )
        except Exception:
            pass
        return self._visible_bbox

    def _start_move(self, event):
        self._press_root = (event.x_root, event.y_root)
        self._drag_moved = False
        self._drag_offset = (
            event.x_root - self.window.winfo_x(),
            event.y_root - self.window.winfo_y(),
        )

    def _do_move(self, event):
        if (
            abs(event.x_root - self._press_root[0]) > self._scale(4)
            or abs(event.y_root - self._press_root[1]) > self._scale(4)
        ):
            self._drag_moved = True
        dx, dy = getattr(self, "_drag_offset", (0, 0))
        self.window.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")
        self._position_hint_window()
        self._update_qt_hint_anchor()

    def _end_click(self, event):
        if self._drag_moved:
            return

    def _double_click(self, event):
        self._drag_moved = False
        self.poke()

    def poke(self):
        now = time.time()
        if now - self._last_poke_at < 0.35:
            return
        self._last_poke_at = now
        play_effect_once(POKE_EFFECT_PATH)
        self.posture_reminder()

    def say(self, text, seconds=6):
        if USE_QT_POPUP:
            if self._show_qt_hint(text, seconds):
                self.message = ""
                self.message_until = 0
                self._hide_message()
                return
        self.message = text
        self.message_until = time.time() + seconds

    def _show_qt_hint(self, text, seconds=6):
        # [CLAUDE] exe 模式下不能用子进程，直接回退到 Tkinter hint
        if getattr(sys, "frozen", False):
            return False
        try:
            if self._qt_hint_process and self._qt_hint_process.poll() is None:
                self._qt_hint_process.terminate()
        except Exception:
            pass
        self._cleanup_qt_hint_anchor()
        try:
            self.window.update_idletasks()
            anchor = self._qt_anchor("hint")
            anchor_path = make_anchor_file(anchor)
            args = [
                python_launcher(),
                os.path.join(APP_DIR, "qt_popup_demo.py"),
                "--hint",
                "--text", text,
                "--duration", str(seconds),
                "--anchor-x", str(anchor[0]),
                "--anchor-y", str(anchor[1]),
                "--anchor-file", anchor_path,
            ]
            self._qt_hint_process = subprocess.Popen(
                args,
                cwd=APP_DIR,
                creationflags=qt_creation_flags(),
            )
            self._qt_hint_anchor_path = anchor_path
            self._qt_hint_until = time.time() + seconds
            return True
        except Exception:
            self._cleanup_qt_hint_anchor()
            return False

    def _cleanup_qt_hint_anchor(self):
        if not self._qt_hint_anchor_path:
            return
        try:
            os.remove(self._qt_hint_anchor_path)
        except OSError:
            pass
        self._qt_hint_anchor_path = None
        self._qt_hint_until = 0

    def _update_qt_hint_anchor(self):
        if not self._qt_hint_anchor_path:
            return
        if time.time() > self._qt_hint_until:
            self._cleanup_qt_hint_anchor()
            return
        try:
            if self._qt_hint_process and self._qt_hint_process.poll() is None:
                write_anchor_file(self._qt_hint_anchor_path, self._qt_anchor("hint"))
            else:
                self._cleanup_qt_hint_anchor()
        except Exception:
            pass

    def _paint_pet_image(self, image):
        from PIL import ImageTk

        photo = ImageTk.PhotoImage(image)
        self._pet_photo = photo
        if self._pet_image_item is None:
            self._pet_image_item = self.canvas.create_image(0, 0, image=photo, anchor="nw")
        else:
            self.canvas.itemconfigure(self._pet_image_item, image=photo)
            try:
                self.canvas.tag_lower(self._pet_image_item)
            except tk.TclError:
                pass

    def _redraw_now(self):
        try:
            self._transition_left = 0
            cur_name = self._sprite_name()
            cur_img = self._render_sprite()
            self._last_sprite_name = cur_name
            self._last_sprite_img = cur_img

            self._paint_pet_image(cur_img)
            self._draw_message()
            self.canvas.update_idletasks()
        except Exception:
            pass

    def posture_reminder(self):
        self.mode = "upright"
        self.auto_revert_at = time.time() + 8
        self._popup_anchor_local = None
        self._redraw_now()
        self.say("腰挺直一点。", 8)

    # [CLAUDE] stand_reminder 记录模式开始帧，用于跳过起身过渡帧
    def stand_reminder(self):
        self.mode = "stretch"
        self._mode_start_frame = self.frame
        self.auto_revert_at = 0
        self._popup_anchor_local = self._stable_stand_popup_anchor()
        self._redraw_now()

    def snoozed(self, count):
        self.anger_level = 0
        self.mode = "idle"
        self._popup_anchor_local = None
        self.message = ""
        self.message_until = 0

    def reset_cycle(self):
        self.mode = "idle"
        self.anger_level = 0
        self.auto_revert_at = 0
        self._popup_anchor_local = None
        self.message = ""
        self.message_until = 0

    def stop(self):
        try:
            if self._qt_hint_process and self._qt_hint_process.poll() is None:
                self._qt_hint_process.terminate()
        except Exception:
            pass
        self._cleanup_qt_hint_anchor()
        try:
            self.hint_window.destroy()
        except tk.TclError:
            pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # [CLAUDE] stretch 模式：起身过渡帧只播一次，之后只循环站姿两帧
    def _sprite_name(self):
        if self.mode == "upright":
            return "sit_upright"
        if self.mode == "stretch":
            elapsed = self.frame - getattr(self, "_mode_start_frame", self.frame)
            # 前 5 帧播 getting_up 过渡，之后只循环 stretch_up 和 neck
            if elapsed < 5:
                return "stand_getting_up"
            cycle = (elapsed - 5) // 5
            frames = ["stand_stretch_up", "stand_neck"]
            return frames[cycle % len(frames)]
        if self.mode == "angry":
            frames = ["sit_lean_bad", "sit_relaxed", "sit_cross_leg"]
            return frames[(self.frame // 8) % len(frames)]
        if self.mode == "locked":
            return "sit_relaxed"

        frames = ["sit_lean_bad", "sit_relaxed", "sit_cross_leg"]
        return frames[(self.frame // 8) % len(frames)]

    # [CLAUDE] 支持多级红温精灵：sit_relaxed_l2.png 优先于贴滤镜
    def _load_sprite(self, name, anger_level=0):
        from PIL import Image

        # 尝试加载 anger 等级精灵（如 sit_relaxed_l3.png）
        if anger_level > 0:
            leveled_name = f"{name}_l{anger_level}"
            path = os.path.join(PET_ASSET_DIR, f"{leveled_name}.png")
            if os.path.exists(path):
                if leveled_name not in self.sprite_cache:
                    self.sprite_cache[leveled_name] = Image.open(path).convert("RGBA")
                return self.sprite_cache[leveled_name]

        # 回退到基础精灵
        if name not in self.sprite_cache:
            path = os.path.join(PET_ASSET_DIR, f"{name}.png")
            if os.path.exists(path):
                self.sprite_cache[name] = Image.open(path).convert("RGBA")
            else:
                raise FileNotFoundError(path)
        return self.sprite_cache[name]

    # [CLAUDE] 优先加载等级红温精灵，没有则回退到滤镜叠加
    def _render_sprite(self):
        from PIL import Image

        sprite_name = self._sprite_name()
        sprite_anger = self.anger_level if ENABLE_RED_HEAT else 0
        used_leveled = False

        try:
            # [CLAUDE] 有 anger 时先尝试加载 _l{n} 等级精灵
            sprite = self._load_sprite(sprite_name, sprite_anger)
            # 判断是否加载到了等级精灵（检查缓存key）
            if sprite_anger > 0:
                leveled_name = f"{sprite_name}_l{sprite_anger}"
                used_leveled = leveled_name in self.sprite_cache
        except Exception:
            return render_pet_frame_3d(
                self.logical_w, self.logical_h, self.ui_scale,
                self.mode, self.frame, self.anger_level,
            )

        scale = min(self.w / sprite.width, self.h / sprite.height)
        scaled = sprite.resize(
            (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGBA", (self.w, self.h), (*_hex_to_rgb(self.transparent), 0))
        x = (self.w - scaled.width) // 2
        y = self.h - scaled.height
        bbox = scaled.getbbox()
        if bbox:
            self._visible_bbox = (
                x + bbox[0],
                y + bbox[1],
                x + bbox[2],
                y + bbox[3],
            )
        canvas.alpha_composite(scaled, (x, y))

        # [CLAUDE] 只有没加载到等级精灵时才用滤镜叠加
        if ENABLE_RED_HEAT and self.anger_level and not used_leveled:
            self._add_anger_overlay(canvas, sprite_name, x, y, scale)
        self._prepare_for_transparent_window(canvas)
        return canvas

    def _prepare_for_transparent_window(self, image):
        key = _hex_to_rgb(self.transparent)
        pixels = image.load()
        for y in range(image.height):
            for x in range(image.width):
                r, g, b, a = pixels[x, y]
                if a < 34:
                    pixels[x, y] = (*key, 0)
                elif a < 255:
                    if max(r, g, b) - min(r, g, b) < 36 and max(r, g, b) > 115:
                        mix = 0.88 * ((255 - a) / 255)
                        r = round(r + (255 - r) * mix)
                        g = round(g + (255 - g) * mix)
                        b = round(b + (255 - b) * mix)
                    pixels[x, y] = (r, g, b, 255)

    def _add_anger_overlay(self, image, sprite_name, offset_x, offset_y, scale):
        from PIL import ImageDraw

        anchors = {
            "sit_lean_bad": (206, 98, 66),
            "sit_relaxed": (239, 92, 61),
            "sit_cross_leg": (226, 92, 60),
            "sit_upright": (176, 93, 61),
            "stand_getting_up": (113, 110, 78),
            "stand_stretch_up": (174, 145, 80),
            "stand_neck": (211, 123, 84),
        }
        ax, ay, ar = anchors.get(sprite_name, anchors["sit_relaxed"])
        cx = offset_x + ax * scale
        cy = offset_y + ay * scale
        radius = max(8, ar * scale)

        level = max(1, min(MAX_SNOOZE_COUNT, self.anger_level))
        heat = level / MAX_SNOOZE_COUNT
        tint = (255, 68, 58)
        tint_strength = 0.18 + 0.58 * heat
        pixels = image.load()

        x0 = max(0, int(cx - radius))
        x1 = min(image.width, int(cx + radius) + 1)
        y0 = max(0, int(cy - radius))
        y1 = min(image.height, int(cy + radius) + 1)
        for y in range(y0, y1):
            for x in range(x0, x1):
                r, g, b, a = pixels[x, y]
                if a <= 90:
                    continue
                dx = (x - cx) / radius
                dy = (y - cy) / radius
                if dx * dx + dy * dy > 1.0:
                    continue
                if r < 175 or g < 175 or b < 175:
                    continue
                pixels[x, y] = (
                    round(r + (tint[0] - r) * tint_strength),
                    round(g + (tint[1] - g) * tint_strength),
                    round(b + (tint[2] - b) * tint_strength),
                    a,
                )

        if level >= MAX_SNOOZE_COUNT:
            d = ImageDraw.Draw(image)
            left = [
                (cx - radius * 0.78, cy - radius * 0.72),
                (cx - radius * 0.35, cy - radius * 1.28),
                (cx - radius * 0.14, cy - radius * 0.66),
            ]
            right = [
                (cx + radius * 0.78, cy - radius * 0.72),
                (cx + radius * 0.35, cy - radius * 1.28),
                (cx + radius * 0.14, cy - radius * 0.66),
            ]
            d.polygon(left, fill="#F97316")
            d.polygon(right, fill="#F97316")

    def _position_hint_window(self):
        if not self.message:
            return
        self.window.update_idletasks()
        x = self.window.winfo_x() + (self.w - self.hint_w) // 2
        above_y = self.window.winfo_y() - self.hint_h - self._scale(8)
        if above_y >= self._scale(8):
            y = above_y
        else:
            y = self.window.winfo_y() + self.h + self._scale(6)
        self.hint_window.geometry(f"{self.hint_w}x{self.hint_h}+{x}+{y}")

    def _hide_message(self):
        try:
            self.hint_window.withdraw()
        except tk.TclError:
            pass

    def _draw_message(self):
        if not self.message or time.time() > self.message_until:
            self.message = ""
            self._hide_message()
            return

        if USE_LAYERED_POPUP and sys.platform == "win32":
            from PIL import ImageDraw

            img = render_rounded_rect(
                self.hint_w, self.hint_h, self._scale(14),
                "#FFFFFF", "#E7EAEE", 1, scale=self.ui_scale,
            )
            draw = ImageDraw.Draw(img)
            draw_center_text(
                draw,
                (0, 0, self.hint_w, self.hint_h),
                self.message,
                pil_font(self._scale(8), bold=True),
                "#202124",
            )
            self._position_hint_window()
            try:
                self.hint_window.deiconify()
                self.hint_window.update_idletasks()
                apply_layered_window_image(
                    self.hint_window,
                    img,
                    self.hint_window.winfo_x(),
                    self.hint_window.winfo_y(),
                )
            except tk.TclError:
                pass
            return

        self.hint_canvas.delete("all")
        self.hint_canvas._image_refs = []
        bubble = render_rounded_rect(
            self.hint_w, self.hint_h, self._scale(14),
            "#FFFFFF", "#E7EAEE", 1, scale=self.ui_scale,
        )
        canvas_image(self.hint_canvas, 0, 0, bubble)
        self.hint_canvas.create_text(
            self.hint_w // 2, self.hint_h // 2,
            text=self.message,
            fill="#202124",
            font=("Microsoft YaHei UI", 8, "bold"),
            width=self.hint_w - self._scale(16),
            justify="center",
        )
        self._position_hint_window()
        try:
            self.hint_window.deiconify()
        except tk.TclError:
            pass

    # [CLAUDE] 重写 _animate：交叉淡入淡出 + 全屏时自动隐藏
    def _animate(self):
        from PIL import Image

        now = time.time()

        # [CLAUDE] 全屏检测：视频/游戏/PPT 时隐藏桌宠
        fullscreen_active = is_fullscreen_active()
        if fullscreen_active:
            self._fullscreen_hits += 1
        else:
            self._fullscreen_hits = 0

        # 连续命中 6 次（约 480ms）才判定为真全屏，避免瞬间误判
        if fullscreen_active and self._fullscreen_hits >= 6:
            try:
                self.window.withdraw()
                self.hint_window.withdraw()
                self._hidden_for_fullscreen = True
            except tk.TclError:
                pass
            self.root.after(80, self._animate)  # [CLAUDE] 快速轮询，几乎无感
            return
        elif not fullscreen_active and self._hidden_for_fullscreen:
            try:
                self.window.deiconify()
                self._hidden_for_fullscreen = False
            except tk.TclError:
                pass

        if self.auto_revert_at and now >= self.auto_revert_at:
            self.mode = "idle"
            self.auto_revert_at = 0

        cur_name = self._sprite_name()
        cur_img = self._render_sprite()

        # [CLAUDE] 交叉淡入淡出：精灵名变化时触发过渡
        if cur_name != self._last_sprite_name and self._last_sprite_img is not None:
            self._transition_left = self.TRANSITION_FRAMES

        if self._transition_left > 0 and self._last_sprite_img is not None:
            alpha = self._transition_left / self.TRANSITION_FRAMES
            blended = Image.blend(cur_img, self._last_sprite_img, alpha)
            display_img = blended
            self._transition_left -= 1
        else:
            display_img = cur_img

        self._last_sprite_name = cur_name
        self._last_sprite_img = cur_img

        self._paint_pet_image(display_img)
        self._draw_message()

        self.frame += 1
        self.root.after(420, self._animate)


# ╔══════════════════════════════════════════════════════╗
# ║  系统托盘                                            ║
# ╚══════════════════════════════════════════════════════╝
def create_tray_icon(trigger_callback, exit_callback):
    import pystray
    from PIL import Image, ImageDraw

    def create_icon():
        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

        bg = render_gradient_rect(size, size, ["#6D8FE6", "#8B79D2", "#6BAE8B"])
        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([28, 28, 228, 228], radius=58, fill=255)
        img.paste(bg, (0, 0), mask)

        d = ImageDraw.Draw(img)
        # 托盘图标会被系统缩得很小，所以椅子主体要足够粗和简单。
        d.rounded_rectangle([62, 48, 194, 112], radius=22, fill="white")
        d.rounded_rectangle([52, 122, 204, 168], radius=21, fill="white")
        d.rounded_rectangle([70, 101, 96, 136], radius=12, fill="white")
        d.rounded_rectangle([160, 101, 186, 136], radius=12, fill="white")
        d.rounded_rectangle([78, 162, 108, 220], radius=14, fill="white")
        d.rounded_rectangle([150, 162, 180, 220], radius=14, fill="white")

        return img.resize((64, 64), Image.Resampling.LANCZOS)

    def selected_ringtone(path):
        return os.path.abspath(path) == os.path.abspath(_RINGTONE_PATH)

    def choose_ringtone(path):
        def handler(icon, _item):
            set_ringtone(path, preview=True)
            try:
                icon.update_menu()
            except Exception:
                pass
        return handler

    def selected_effect(path):
        return os.path.abspath(path) == os.path.abspath(POKE_EFFECT_PATH)

    def choose_effect(path):
        def handler(icon, _item):
            set_interaction_effect(path, preview=True)
            try:
                icon.update_menu()
            except Exception:
                pass
        return handler

    def ringtone_menu_items():
        paths = get_ringtone_paths()
        if paths:
            for path in paths:
                yield pystray.MenuItem(
                    ringtone_name(path),
                    choose_ringtone(path),
                    checked=lambda _item, p=path: selected_ringtone(p),
                    radio=True,
                )
            yield pystray.Menu.SEPARATOR
        else:
            yield pystray.MenuItem("未找到 WAV 铃声", None, enabled=False)
        yield pystray.MenuItem("试听当前铃声", lambda _icon, _item: set_ringtone(_RINGTONE_PATH, preview=True))
        yield pystray.MenuItem("打开铃声文件夹", lambda _icon, _item: open_ringtone_folder())

    def effect_menu_items():
        paths = get_effect_paths()
        if paths:
            for path in paths:
                yield pystray.MenuItem(
                    effect_name(path),
                    choose_effect(path),
                    checked=lambda _item, p=path: selected_effect(p),
                    radio=True,
                )
            yield pystray.Menu.SEPARATOR
        else:
            yield pystray.MenuItem("未找到 WAV 音效", None, enabled=False)
        yield pystray.MenuItem("试听当前音效", lambda _icon, _item: play_effect_once(POKE_EFFECT_PATH))
        yield pystray.MenuItem("打开音效文件夹", lambda _icon, _item: open_effect_folder())

    icon = pystray.Icon(
        APP_NAME, create_icon(), APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem("立即提醒", lambda _: trigger_callback(), default=True),
            pystray.MenuItem("铃声", pystray.Menu(ringtone_menu_items)),
            pystray.MenuItem("互动音效", pystray.Menu(effect_menu_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda _: exit_callback()),
        )
    )
    return icon


# ╔══════════════════════════════════════════════════════╗
# ║  入口                                                ║
# ╚══════════════════════════════════════════════════════╝
def main():
    global _active_seconds, _next_reminder_at

    enable_dpi_awareness()
    load_settings()

    if already_running():
        sys.exit(0)

    # 隐藏的 Tk root，用于调度和弹窗（主线程）
    root = tk.Tk()
    tune_tk_scaling(root)
    root.withdraw()  # 不显示空窗口

    stop_event = threading.Event()
    pet = DesktopPet(root)
    snooze_count = 0
    _active_posture_seconds = 0  # [CLAUDE] 活跃坐姿累计（秒）
    last_timer_check = time.monotonic()

    # [CLAUDE] 活跃计时：仅在用户操作鼠标/键盘时累计
    IDLE_FREEZE_SEC = IDLE_PAUSE_MINUTES * 60   # 超过 → 冻结计时
    IDLE_RESET_SEC = IDLE_RESET_MINUTES * 60     # 超过 → 清零（真离开）
    STAND_TARGET   = INTERVAL_MINUTES * 60
    POSTURE_TARGET = POSTURE_REMINDER_MINUTES * 60

    def start_forced_rest():
        nonlocal snooze_count, _active_posture_seconds
        global _active_seconds, _next_reminder_at
        stop_sound()
        snooze_count = 0
        _next_reminder_at = 0
        pet.reset_cycle()
        _active_seconds = 0
        _active_posture_seconds = 0
        show_forced_rest_overlay(root, FORCED_REST_SECONDS)
        pet.reset_cycle()
        _active_seconds = 0
        _active_posture_seconds = 0

    # ——— 开始提醒（必须在主线程调用） ———
    def trigger_reminder():
        nonlocal snooze_count, _active_posture_seconds
        global _active_seconds, _next_reminder_at
        if snooze_count >= FORCED_REST_AFTER_SNOOZES:
            start_forced_rest()
            return

        pet.stand_reminder()
        action = show_popup(root, snooze_count, MAX_SNOOZE_COUNT, pet.window)

        if action == "snooze" and snooze_count < FORCED_REST_AFTER_SNOOZES:
            snooze_count += 1
            pet.snoozed(snooze_count)
            _active_seconds = 0
            _active_posture_seconds = 0
            _next_reminder_at = time.monotonic() + SNOOZE_MINUTES * 60
        else:
            snooze_count = 0
            pet.reset_cycle()
            _active_seconds = 0
            _active_posture_seconds = 0
            _next_reminder_at = 0

    # ——— 线程安全包装：从任意线程触发 ———
    def safe_trigger():
        root.after(0, trigger_reminder)

    # ——— 定时检查（[CLAUDE] 重写：空闲分级处理） ———
    def check_timer():
        nonlocal _active_posture_seconds, last_timer_check, snooze_count
        global _active_seconds, _next_reminder_at

        if not stop_event.is_set():
            now_monotonic = time.monotonic()
            tick = min(
                max(0.0, now_monotonic - last_timer_check),
                (_CHECK_EVERY_MS / 1000.0) * 2,
            )
            last_timer_check = now_monotonic
            idle = get_idle_seconds()
            next_check_ms = _CHECK_EVERY_MS

            if is_workstation_locked():
                # [CLAUDE] 锁屏 = 人肯定不在 → 清零
                _active_seconds = 0
                _active_posture_seconds = 0
                snooze_count = 0
                _next_reminder_at = 0
            elif idle >= IDLE_RESET_SEC:
                # 长时间离开 → 清零，回来重新算
                _active_seconds = 0
                _active_posture_seconds = 0
                snooze_count = 0
                _next_reminder_at = 0
            elif _next_reminder_at:
                if now_monotonic >= _next_reminder_at:
                    _next_reminder_at = 0
                    if snooze_count >= FORCED_REST_AFTER_SNOOZES:
                        start_forced_rest()
                    else:
                        trigger_reminder()
            elif idle < IDLE_FREEZE_SEC:
                # 用户活跃 → 累计久坐时间
                _active_seconds += tick
                _active_posture_seconds += tick

                if _active_seconds >= STAND_TARGET:
                    trigger_reminder()
                elif _active_posture_seconds >= POSTURE_TARGET:
                    pet.posture_reminder()
                    _active_posture_seconds = 0
            elif idle < IDLE_RESET_SEC:
                # 短暂走开 → 冻结不计时，但不重置
                pass

            if _next_reminder_at:
                remaining_ms = round((_next_reminder_at - time.monotonic()) * 1000)
                next_check_ms = min(_CHECK_EVERY_MS, max(100, remaining_ms))
            root.after(next_check_ms, check_timer)

    # ——— 退出 ———
    def on_exit():
        stop_sound()
        stop_event.set()

        def finish_exit():
            tray_icon.stop()
            pet.stop()
            root.destroy()

        root.after(0, finish_exit)

    # ——— 启动 ———
    _active_seconds = 0
    _next_reminder_at = 0
    _active_posture_seconds = 0

    tray_icon = create_tray_icon(safe_trigger, on_exit)
    tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
    tray_thread.start()

    print(f"{APP_NAME} running in system tray")
    print(f"  interval: {INTERVAL_MINUTES} min  |  right-click to exit")

    # 启动时预览一次通知
    if SHOW_ON_START:
        root.after(500, trigger_reminder)

    # 开始定时检查
    root.after(_CHECK_EVERY_MS, check_timer)

    root.mainloop()


if __name__ == "__main__":
    if "--once" in sys.argv:
        start_sound()
        time.sleep(INTERVAL_MINUTES * 2)  # fallback
        stop_sound()
    else:
        main()
