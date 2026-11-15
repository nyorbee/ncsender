"""Makes the README screenshots (docs/*.png) from the REAL program, on Windows.

Run from the repo folder:   python make_screenshots.py
- needs Pillow (pip install pillow) and the same packages as ncsender2.py
- fills the window with EXAMPLE data only; your saved settings and passwords are never read,
  changed or deleted (profile redirected to a temp folder, keyring and config writes disabled)
- captures only the program windows (cropped to the window frame), not your desktop
- do not touch the mouse/keyboard and keep other windows away until it finishes (~15 s)
"""
import os, sys, json, time, tempfile, threading, importlib.util, http.server

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs")
os.makedirs(OUT, exist_ok=True)

# never touch the real config / keyring entries
fake = tempfile.mkdtemp(prefix="ncshots_")
for k in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"):
    os.environ[k] = fake

try:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
except Exception:
    ctypes = None
from PIL import ImageGrab
import tkinter as tk
from tkinter import ttk

spec = importlib.util.spec_from_file_location("ncs", os.path.join(HERE, "ncsender2.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# hard safety: the config file must be the temp one, and the keyring is made read-nothing/write-nothing
assert os.path.abspath(m.CONFIG_FILE).startswith(os.path.abspath(fake)), "refusing to run: config path is not the temp one"
m.kr_set = lambda *a, **k: True
m.kr_delete = lambda *a, **k: None
m.kr_get = lambda *a, **k: ""
m.save_config = lambda *a, **k: None

B = "https://cloud.example.com"
SHARES = [
    {"id": "1", "share_type": 3, "path": "/SHARE/Holiday-2026.mp4", "item_type": "file", "label": "for Anna", "expiration": "2026-12-31 00:00:00", "password": "x", "url": f"{B}/s/holiday", "stime": 1763000000},
    {"id": "2", "share_type": 3, "path": "/SHARE/Photos", "item_type": "folder", "label": "", "expiration": None, "password": None, "url": f"{B}/s/photos-2026", "stime": 1762500000},
    {"id": "3", "share_type": 3, "path": "/SHARE/Backup-2026.zip", "item_type": "file", "label": "backup", "expiration": "2027-01-15 00:00:00", "password": None, "url": f"{B}/s/k7Xq2mPaVb", "stime": 1762000000},
    {"id": "4", "share_type": 3, "path": "/Documents/Report.pdf", "item_type": "file", "label": "", "expiration": None, "password": "x", "url": f"{B}/s/q9WzLr4TeY", "stime": 1760000000},
    {"id": "5", "share_type": 3, "path": "/SHARE/Installer.exe", "item_type": "file", "label": "tools", "expiration": "2026-11-30 00:00:00", "password": None, "url": f"{B}/s/tools", "stime": 1755000000},
]


class H(http.server.BaseHTTPRequestHandler):          # fake Nextcloud share API on localhost
    def do_GET(self):
        b = json.dumps({"ocs": {"meta": {"statuscode": 200}, "data": SHARES}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
API = f"http://127.0.0.1:{srv.server_port}/ocs/v2.php/apps/files_sharing/api/v1/shares"

App = getattr(m, "NextcloudSFTPUploaderPlain", None) or m.NextcloudSFTPUploader
m._Uploader._show_startup_msg = lambda self: None
root = App()


# This script drives Tk with its own loop instead of mainloop(), and newer Python versions refuse
# root.after() calls coming from worker threads in that case. So thread-side after() calls are
# queued here and run from pump(). (The real program uses mainloop and is not affected.)
import queue
_q = queue.Queue()
_orig_after = root.after


def _after(ms, fn=None, *args):
    if threading.current_thread() is not threading.main_thread():
        if fn is not None:
            _q.put((fn, args))
        return None
    return _orig_after(ms, fn, *args) if fn is not None else _orig_after(ms)


root.after = _after


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        while not _q.empty():
            fn, args = _q.get()
            fn(*args)
        root.update()
        time.sleep(0.02)


def frame_box(w):
    """Window rectangle including the title bar and borders (Windows), else the client area."""
    w.update_idletasks()
    if ctypes is not None:
        try:
            from ctypes import wintypes
            hwnd = ctypes.windll.user32.GetParent(w.winfo_id()) or w.winfo_id()
            r = wintypes.RECT()
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r))   # EXTENDED_FRAME_BOUNDS
            if r.right > r.left:
                # DWM bounds can reach a few pixels below the visible window (pixels of whatever is
                # behind it), so take the bottom/sides from Tk's own client size + 1 px border
                cx, cy = w.winfo_rootx(), w.winfo_rooty()
                cw, ch = w.winfo_width(), w.winfo_height()
                if abs(cx - r.left) <= 16 and 0 < cy - r.top < 120:
                    return (cx - 1, r.top, cx + cw + 1, cy + ch + 1)
                return (r.left, r.top, r.right, r.bottom)
        except Exception:
            pass
    x, y = w.winfo_rootx(), w.winfo_rooty()
    return (x, y, x + w.winfo_width(), y + w.winfo_height())


def grab_window(w):
    """Windows: ask the window itself for its picture (PrintWindow), so not a single pixel of the
    desktop behind it can end up in the image. Returns an RGB image or None."""
    if ctypes is None or not hasattr(ctypes, "windll"):
        return None
    from ctypes import wintypes
    from PIL import Image
    user32, gdi32, dwm = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.dwmapi
    user32.GetWindowDC.restype = wintypes.HDC
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    w.update_idletasks()
    hwnd = user32.GetParent(w.winfo_id()) or w.winfo_id()
    wr, er = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(wr))
    dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(er), ctypes.sizeof(er))
    W, H = wr.right - wr.left, wr.bottom - wr.top
    hdc = user32.GetWindowDC(wintypes.HWND(hwnd))
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, W, H)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(wintypes.HWND(hwnd), mdc, 2):      # 2 = PW_RENDERFULLCONTENT
            return None
        bih = BIH(ctypes.sizeof(BIH), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(W * H * 4)
        if not gdi32.GetDIBits(mdc, bmp, 0, H, buf, ctypes.byref(bih), 0):
            return None
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(wintypes.HWND(hwnd), hdc)
    img = Image.frombuffer("RGB", (W, H), bytes(buf), "raw", "BGRX", 0, 1)
    box = (er.left - wr.left, er.top - wr.top, er.right - wr.left, er.bottom - wr.top)
    img = img.crop(box)
    lo, hi = img.convert("L").getextrema()
    return img if hi > 20 else None                                  # all black = capture failed


def shot(w, name):
    w.attributes("-topmost", True)
    w.lift()
    pump(0.6)
    img = None
    try:
        img = grab_window(w)
    except Exception as e:
        print("PrintWindow failed, using screen grab:", e)
    if img is None:
        img = ImageGrab.grab(bbox=frame_box(w), all_screens=True)
    img = img.convert("RGBA")
    # make the rounded window corners transparent, so no pixels of your desktop end up in the file
    from PIL import Image, ImageDraw
    r = max(8, int(9 * (root.winfo_fpixels("1i") / 96.0)))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius=r, fill=255)
    img.putalpha(mask)
    img.save(os.path.join(OUT, name))
    print("saved", name)


def toplevels():
    return [x for x in root.winfo_children() if isinstance(x, tk.Toplevel)]


def find(w, cls):
    out = []
    for c in w.winfo_children():
        if isinstance(c, cls):
            out.append(c)
        out += find(c, cls)
    return out


# ── main window with example data ────────────────────────────────────────────
root.geometry("+40+40")
root.ssh_host_var.set("192.168.1.100")
root.ssh_user_var.set("user")
root.auth_var.set("key")
root._on_auth_change()
root.key_path_var.set(r"C:\Users\you\.ssh\id_ed25519")
root.sudo_pass_var.set("x" * 16)
root.remember_var.set(True)
root.remember_sudo_var.set(True)
root.nc_user_var.set("user")
root.remote_var.set("/SHARE")
root.tmp_var.set("/home/user/.nc_tmp")
root.nc_url_var.set(B)
root.nc_app_pass_var.set("x" * 20)
root.share_token_var.set("holiday")
root.share_days_var.set("30")
root.share_label_var.set("for Anna")
root.local_var.set("3 item(s) selected")
for n in ("Holiday-2026.mp4  (18.4 GB)", "Backup-2026.zip  (4.2 GB)", "Photos/  (312 files)"):
    root.file_list.insert("end", n)
root._set_status("✓ SSH OK  |  ✓ sudo password correct.", m.SUCCESS)
pump(1.0)
# the program asks for an 800 px wide window; with display scaling (125 %, 150 %) that is too
# narrow for the larger fonts, so size it like the real window looks on a scaled screen
scale = root.winfo_fpixels("1i") / 96.0
root.update_idletasks()
ww = max(int(800 * scale), root.winfo_reqwidth() + 20)
hh = root.winfo_reqheight() + int(20 * scale)
root.geometry(f"{ww}x{hh}+40+40")
pump(1.0)
shot(root, "main.png")

root.progress_var.set(62)
root._set_status("Uploading Holiday-2026.mp4 (1 / 3)  ·  11.4 / 18.4 GB", m.FG2)
root.speed_var.set("94.3 MB/s  ·  ETA 1m 14s")
pump(0.5)
shot(root, "uploading.png")

# ── share link dialog ────────────────────────────────────────────────────────
root.progress_var.set(100)
root._set_status("✓ Done! Sent 3 file(s). Nextcloud updated.", m.SUCCESS)
root.speed_var.set("")
root._show_links([("Holiday-2026.mp4", B + "/s/holiday"), ("Backup-2026.zip", B + "/s/k7Xq2mPaVb")])
pump(0.8)
d = toplevels()[0]
d.geometry("+120+120")
shot(d, "share-link.png")
d.destroy()

# ── browse share links ───────────────────────────────────────────────────────
root._open_links_browser({"api": API, "user": "u", "app_pw": "p"})
pump(1.0)
w = toplevels()[0]
w.geometry("+60+60")
tree = find(w, ttk.Treeview)[0]
t0 = time.time()
while not tree.get_children() and time.time() - t0 < 30:      # first HTTPS/SSL setup can take a few seconds
    pump(0.25)
pump(0.5)
kids = tree.get_children()
if not kids:
    print("WARNING: the link list did not load - browse.png would be empty")
if len(kids) > 1:
    tree.selection_set(kids[1])
pump(0.3)
shot(w, "browse.png")

root.destroy()
print("Done. New screenshots are in:", OUT)
