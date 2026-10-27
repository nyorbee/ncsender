#!/usr/bin/env python3
"""
Nextcloud SFTP Uploader v18
Changes from v17:
- SSH host key verification: the first connection to a server asks you to confirm its
  fingerprint and remembers it (~/.nextcloud_sftp_uploader_known_hosts); if the key of a known
  server ever changes, the connection is stopped with a warning BEFORE any password is sent
Changes in v17 (from v16):
- New field "Server temp dir" (default /tmp) - choose a folder with enough free space
- Pre-flight check: free space on the server temp dir vs. total upload size
- On upload failure: shows how far it got + free space on server; writes ~/NCSender_upload.log
- "Clean tmp" uses the selected temp dir
Changes in v16 (from v15):
- Drag & drop failure now reports the real underlying Tcl error and writes
  ~/NCSender_dnd.log (traceback + list of files found in the tkdnd folder)
Changes in v15 (from v14):
- Window stays hidden until fully built and sized (no small window flashing/resizing at start)
Changes in v14 (from v13):
- Fixes the stray empty "tk" window left over when drag & drop failed to load
- Shows the reason in the status bar when drag & drop is unavailable
Changes in v13 (from v12, fixes for compiled .exe):
- Never runs pip when frozen/compiled (sys.executable is the exe itself -> would relaunch it)
- Drag & drop falls back to plain Tk if tkdnd cannot be loaded
- Startup crashes are shown in a message box and saved to ~/NCSender_error.log
- keyring: explicitly selects the Windows Credential Manager backend
Changes in v12 (from v11):
- Drag & drop files/folders onto the window to add them to the send list (tkinterdnd2)
- Dropped/picked items accumulate; new "Clear" button empties the list
Changes in v11 (from v10):
- Passwords (SSH password, key passphrase, sudo password) are stored in the system
  keyring (Windows Credential Manager / macOS Keychain / Secret Service) instead of
  the JSON file. The JSON keeps only non-sensitive settings.
- Automatic migration of old plaintext passwords from the JSON file into the keyring
- "Forget saved passwords" button
- If no keyring backend is available, passwords are NOT saved (never falls back to plaintext)
- Sudo plaintext warning dialog removed (replaced by a short note)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os, sys, json, time, socket, secrets, io, base64, hashlib

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".nextcloud_sftp_uploader.json")
KR_SERVICE  = "NextcloudSFTPUploader"
KNOWN_HOSTS_FILE = os.path.join(os.path.expanduser("~"), ".nextcloud_sftp_uploader_known_hosts")

BG      = "#0f1117"
BG2     = "#1a1d27"
BG3     = "#22263a"
ACCENT  = "#4f8ef7"
ACCENT2 = "#7c3aed"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
ERROR   = "#ef4444"
FG      = "#e2e8f0"
FG2     = "#94a3b8"
FONT    = ("Consolas", 10)
FONT_SM = ("Consolas", 9)
FONT_LG = ("Consolas", 12, "bold")
FONT_XL = ("Consolas", 15, "bold")


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def is_frozen():
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def install_if_missing(pkg):
    try:
        __import__(pkg)
    except ImportError:
        if is_frozen():
            raise            # inside an exe: never try to pip install
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])


# ── Drag & drop support (optional – falls back to plain Tk if unavailable) ───
try:
    install_if_missing("tkinterdnd2")
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _TkBase = TkinterDnD.Tk
    DND_OK  = True
except Exception:
    _TkBase = tk.Tk
    DND_OK  = False
DND_ERR = ""


# ── Keyring helpers ──────────────────────────────────────────────────────────

def kr_available():
    try:
        import keyring
        if sys.platform == "win32":
            try:
                from keyring.backends.Windows import WinVaultKeyring
                keyring.set_keyring(WinVaultKeyring())
            except Exception:
                pass
        be = keyring.get_keyring()
        if be.__class__.__module__.startswith("keyring.backends.fail"):
            return False
        return getattr(be, "priority", 1) > 0
    except Exception:
        return False


def kr_get(name):
    try:
        import keyring
        return keyring.get_password(KR_SERVICE, name) or ""
    except Exception:
        return ""


def kr_set(name, value):
    try:
        import keyring
        keyring.set_password(KR_SERVICE, name, value)
        return True
    except Exception:
        return False


def kr_delete(name):
    try:
        import keyring
        keyring.delete_password(KR_SERVICE, name)
    except Exception:
        pass


LOG_FILE = os.path.join(os.path.expanduser("~"), "NCSender_upload.log")


def log_line(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def q(s):
    """Shell single-quote escape."""
    return s.replace("'", "'\\''")


def hostkey_name(host, port):
    """Name under which a server is stored in the known-hosts file (OpenSSH convention)."""
    return host if int(port) == 22 else f"[{host}]:{port}"


def host_fingerprint(key):
    """SHA256 fingerprint of a host key, in the same format as `ssh-keygen -lf`."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def default_key_path():
    ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
    for name in ("id_ed25519", "id_rsa", "id_ecdsa"):
        p = os.path.join(ssh_dir, name)
        if os.path.isfile(p):
            return p
    return ""


class _Uploader:
    def __init__(self):
        super().__init__()
        self.withdraw()          # keep hidden until fully built and sized
        self.title("Nextcloud SFTP Uploader")
        self.configure(bg=BG)
        self.resizable(True, True)

        _icon_dir = os.path.dirname(os.path.abspath(__file__))
        for name in ("icon.ico", "icon.png"):
            _p = os.path.join(_icon_dir, name)
            if os.path.exists(_p):
                try:
                    if name.endswith(".ico"):
                        self.iconbitmap(_p)
                    else:
                        self.iconphoto(True, tk.PhotoImage(file=_p))
                except Exception:
                    pass
                break

        self.cfg               = load_config()
        self.kr_ok             = kr_available()
        self.cancel_flag       = threading.Event()
        self.selected_paths    = []
        self.remember_var      = tk.BooleanVar(value=self.cfg.get("remember", False))
        self.remember_sudo_var = tk.BooleanVar(value=self.cfg.get("remember_sudo", False))
        self.auth_var          = tk.StringVar(value="password")   # "password" | "key"
        self._active_chan      = None
        self._min_h            = 0
        self._kr_acct          = ""
        self._startup_msg      = None

        self._build_ui()
        self._setup_dnd()
        self._migrate_plaintext()
        self._load_saved()
        self._on_auth_change(initial=True)
        self._fit_window()
        self.deiconify()
        self.after(150, self._show_startup_msg)

    # ── Layout ───────────────────────────────────────────────────────────────

    def _fit_window(self, extra_h=0):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(800, sw - 80)
        h = min(self.winfo_reqheight() + 20 + extra_h, sh - 80)
        if not self._min_h:
            self._min_h = h
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.minsize(680, self._min_h)

    def _expand_for_status(self):
        self.update_idletasks()
        needed = self.winfo_reqheight() + 20
        cur_h  = self.winfo_height()
        if needed > cur_h:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            new_h  = min(needed, sh - 80)
            x = (sw - self.winfo_width()) // 2
            y = (sh - new_h) // 2
            self.geometry(f"{self.winfo_width()}x{new_h}+{x}+{y}")

    def _section(self, parent, title, row):
        tk.Label(parent, text=title, font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG2).grid(row=row, column=0, sticky="w", padx=16, pady=(10, 2))

    def _field(self, parent, var, row, label, show=""):
        tk.Label(parent, text=label, font=FONT, bg=BG2, fg=FG2,
                 width=20, anchor="w").grid(row=row, column=0, sticky="w", padx=(12, 0), pady=2)
        e = tk.Entry(parent, textvariable=var, show=show,
                     font=FONT, bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=4)
        e.grid(row=row, column=1, sticky="ew", padx=(6, 12), pady=2)
        return e

    def _set_status(self, msg, color=FG2):
        self.status_var.set(msg)
        self.status_lbl.configure(fg=color)
        self.after(10, self._expand_for_status)

    def _show_startup_msg(self):
        if self._startup_msg:
            self._set_status(*self._startup_msg)
        elif DND_ERR:
            self._set_status(f"⚠ Drag & drop disabled: {DND_ERR}  (details: ~/NCSender_dnd.log)", WARNING)
        elif not self.kr_ok:
            self._set_status("⚠ No system keyring available – passwords cannot be saved "
                             "(you'll have to type them each time).", WARNING)

    def _reset_buttons(self):
        self.btn_send.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.btn_cleanup.configure(state="normal")

    def _open_facebook(self, _=None):
        import webbrowser
        webbrowser.open("https://www.facebook.com/ajtiguy")

    # ── Build UI ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        hdr = tk.Frame(self, bg=BG, pady=10)
        hdr.grid(row=0, column=0, sticky="ew", padx=16)
        tk.Label(hdr, text="⬆  NEXTCLOUD SFTP UPLOADER",
                 font=FONT_XL, bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="SFTP → files:scan",
                 font=FONT_SM, bg=BG, fg=FG2).pack(side="left", padx=10)

        _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "created.png")
        if os.path.exists(_logo_path):
            try:
                img = tk.PhotoImage(file=_logo_path).subsample(4, 4)
                self._logo_img = img
                lbl = tk.Label(hdr, image=img, bg=BG, cursor="hand2")
                lbl.pack(side="right", padx=(10, 0))
                lbl.bind("<Button-1>", self._open_facebook)
            except Exception:
                pass

        cols = tk.Frame(self, bg=BG)
        cols.grid(row=1, column=0, sticky="ew")
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=1)

        self._section(cols, "🔑  SSH / SFTP", 0)
        ssh_f = tk.Frame(cols, bg=BG2)
        ssh_f.grid(row=1, column=0, sticky="nsew", padx=(16, 6), pady=(0, 4))
        ssh_f.columnconfigure(1, weight=1)

        self.ssh_host_var  = tk.StringVar()
        self.ssh_port_var  = tk.StringVar(value="22")
        self.ssh_user_var  = tk.StringVar()
        self.ssh_pass_var  = tk.StringVar()
        self.key_path_var  = tk.StringVar()
        self.key_pass_var  = tk.StringVar()
        self.sudo_pass_var = tk.StringVar()

        self._field(ssh_f, self.ssh_host_var,  0, "Host:")
        self._field(ssh_f, self.ssh_port_var,  1, "Port:")
        self._field(ssh_f, self.ssh_user_var,  2, "SSH User:")

        auth_row = tk.Frame(ssh_f, bg=BG2)
        auth_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 0))
        tk.Label(auth_row, text="Login with:", font=FONT, bg=BG2, fg=FG2).pack(side="left", padx=(2, 8))
        for txt, val in (("Password", "password"), ("Key file", "key")):
            tk.Radiobutton(auth_row, text=txt, value=val, variable=self.auth_var,
                           font=FONT, bg=BG2, fg=FG, selectcolor=BG3,
                           activebackground=BG2, activeforeground=FG, cursor="hand2",
                           command=self._on_auth_change).pack(side="left", padx=(0, 10))

        self.pass_f = tk.Frame(ssh_f, bg=BG2)
        self.pass_f.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.pass_f.columnconfigure(1, weight=1)
        self._field(self.pass_f, self.ssh_pass_var, 0, "SSH Password:", show="*")

        self.key_f = tk.Frame(ssh_f, bg=BG2)
        self.key_f.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.key_f.columnconfigure(1, weight=1)
        tk.Label(self.key_f, text="Private key:", font=FONT, bg=BG2, fg=FG2,
                 width=20, anchor="w").grid(row=0, column=0, sticky="w", padx=(12, 0), pady=2)
        tk.Entry(self.key_f, textvariable=self.key_path_var, font=FONT, bg=BG3, fg=FG,
                 insertbackground=FG, relief="flat", bd=4).grid(
                 row=0, column=1, sticky="ew", padx=(6, 4), pady=2)
        tk.Button(self.key_f, text="Browse…", font=FONT_SM, bg=BG3, fg=FG,
                  activebackground=ACCENT, activeforeground="white",
                  relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
                  command=self._pick_key).grid(row=0, column=2, padx=(0, 12))
        self._field(self.key_f, self.key_pass_var, 1, "Key passphrase:", show="*")
        tk.Label(self.key_f, text="passphrase: leave empty if key is not encrypted",
                 font=FONT_SM, bg=BG2, fg=FG2).grid(
                 row=2, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))

        self._field(ssh_f, self.sudo_pass_var, 5, "sudo Password:", show="*")

        tk.Checkbutton(ssh_f, text="Remember sudo password (system keyring)",
                       variable=self.remember_sudo_var,
                       font=FONT_SM, bg=BG2, fg=FG2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=FG, cursor="hand2",
                       command=self._on_remember_sudo_toggle).grid(
                       row=6, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 0))
        tk.Label(ssh_f, text="note: sudo = root access on the server, use with care",
                 font=FONT_SM, bg=BG2, fg=WARNING).grid(
                 row=7, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 2))

        tk.Checkbutton(ssh_f, text="Remember login credentials (system keyring)",
                       variable=self.remember_var,
                       font=FONT_SM, bg=BG2, fg=FG2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=FG, cursor="hand2",
                       command=self._on_remember_toggle).grid(
                       row=8, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))

        btn_row = tk.Frame(ssh_f, bg=BG2)
        btn_row.grid(row=9, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)
        tk.Button(btn_row, text="Test connection",
                  font=FONT, bg=BG3, fg=FG,
                  activebackground=ACCENT, activeforeground="white",
                  relief="flat", bd=0, pady=4, cursor="hand2",
                  command=self._test_connection).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Button(btn_row, text="Forget saved passwords",
                  font=FONT, bg=BG3, fg=WARNING,
                  activebackground=WARNING, activeforeground="white",
                  relief="flat", bd=0, pady=4, cursor="hand2",
                  command=self._forget_saved).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self._section(cols, "☁  NEXTCLOUD", 0)
        nc_f = tk.Frame(cols, bg=BG2)
        nc_f.grid(row=1, column=1, sticky="nsew", padx=(6, 16), pady=(0, 4))
        nc_f.columnconfigure(1, weight=1)

        self.nc_user_var = tk.StringVar()
        self.nc_data_var = tk.StringVar(value="/var/www/html/nextcloud/data")
        self.nc_occ_var  = tk.StringVar(value="/var/www/html/nextcloud/occ")
        self.remote_var  = tk.StringVar(value="/")
        self.tmp_var     = tk.StringVar(value="/tmp")

        self._field(nc_f, self.nc_user_var, 0, "NC User:")
        self._field(nc_f, self.nc_data_var, 1, "data/ path:")
        self._field(nc_f, self.nc_occ_var,  2, "occ path:")
        self._field(nc_f, self.remote_var,  3, "Target folder:")
        self._field(nc_f, self.tmp_var,     4, "Server temp dir:")

        tk.Label(nc_f, text='e.g. "/" or "/SHARE"',
                 font=FONT_SM, bg=BG2, fg=FG2).grid(
                 row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 6))

        self._section(self, "📁  WHAT TO SEND" + ("   (drag & drop files/folders onto the window)" if DND_OK else ""), 2)
        loc = tk.Frame(self, bg=BG2)
        loc.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
        loc.columnconfigure(0, weight=1)

        top = tk.Frame(loc, bg=BG2)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        top.columnconfigure(0, weight=1)

        self.local_var = tk.StringVar()
        tk.Entry(top, textvariable=self.local_var, font=FONT,
                 bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=4).grid(
                 row=0, column=0, sticky="ew")

        for col, (txt, cmd) in enumerate([("File(s)", self._pick_files),
                                          ("Folder",  self._pick_dir),
                                          ("Clear",   self._clear_paths)]):
            tk.Button(top, text=txt, font=FONT_SM, bg=BG3, fg=FG,
                      relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                      command=cmd).grid(row=0, column=col+1, padx=(5, 0))

        lf = tk.Frame(loc, bg=BG3)
        lf.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        lf.columnconfigure(0, weight=1)

        self.file_list = tk.Listbox(lf, font=FONT_SM, bg=BG3, fg=FG2,
                                    selectbackground=ACCENT, selectforeground="white",
                                    relief="flat", bd=0, height=3)
        self.file_list.grid(row=0, column=0, sticky="ew")
        sb = tk.Scrollbar(lf, command=self.file_list.yview, bg=BG3)
        sb.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=sb.set)

        self._section(self, "📊  PROGRESS", 4)
        pf = tk.Frame(self, bg=BG2)
        pf.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 4))
        pf.columnconfigure(0, weight=1)

        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(pf, variable=self.progress_var, maximum=100).grid(
            row=0, column=0, sticky="ew", padx=12, pady=(8, 4))

        info_row = tk.Frame(pf, bg=BG2)
        info_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        info_row.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Ready.")
        self.status_lbl = tk.Label(info_row, textvariable=self.status_var,
                                   font=FONT_SM, bg=BG2, fg=FG2,
                                   anchor="w", wraplength=700, justify="left")
        self.status_lbl.grid(row=0, column=0, sticky="ew")

        self.speed_var = tk.StringVar(value="")
        tk.Label(info_row, textvariable=self.speed_var,
                 font=FONT_SM, bg=BG2, fg=ACCENT, anchor="e").grid(
                 row=0, column=1, sticky="e", padx=(8, 0))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TProgressbar", troughcolor=BG3, background=ACCENT, thickness=10)

        bf = tk.Frame(self, bg=BG)
        bf.grid(row=6, column=0, sticky="ew", padx=16, pady=(4, 14))
        for c in range(3):
            bf.columnconfigure(c, weight=1)

        self.btn_send = tk.Button(bf, text="⬆  SEND VIA SFTP",
                                  font=FONT_LG, bg=ACCENT, fg="white",
                                  activebackground=ACCENT2, activeforeground="white",
                                  relief="flat", bd=0, pady=9, cursor="hand2",
                                  command=self._start_upload)
        self.btn_send.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.btn_cancel = tk.Button(bf, text="✕  Cancel",
                                    font=FONT_LG, bg=BG3, fg=FG2,
                                    activebackground=ERROR, activeforeground="white",
                                    relief="flat", bd=0, pady=9, cursor="hand2",
                                    state="disabled", command=self._cancel)
        self.btn_cancel.grid(row=0, column=1, sticky="ew", padx=(5, 5))

        self.btn_cleanup = tk.Button(bf, text="🗑  Clean tmp",
                                     font=FONT_LG, bg=BG3, fg=WARNING,
                                     activebackground=WARNING, activeforeground="white",
                                     relief="flat", bd=0, pady=9, cursor="hand2",
                                     command=self._start_cleanup)
        self.btn_cleanup.grid(row=0, column=2, sticky="ew", padx=(5, 0))

    # ── Auth UI ──────────────────────────────────────────────────────────────

    def _on_auth_change(self, initial=False):
        if self.auth_var.get() == "key":
            self.pass_f.grid_remove()
            self.key_f.grid()
            if not self.key_path_var.get().strip():
                self.key_path_var.set(default_key_path())
        else:
            self.key_f.grid_remove()
            self.pass_f.grid()
        if not initial:
            self._min_h = 0
            self.after(10, self._fit_window)
            if self.remember_var.get():
                self._save_cfg()

    def _pick_key(self):
        ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
        path = filedialog.askopenfilename(
            title="Select private key file",
            initialdir=ssh_dir if os.path.isdir(ssh_dir) else os.path.expanduser("~"))
        if path:
            self.key_path_var.set(path)

    # ── Config + keyring ─────────────────────────────────────────────────────

    @staticmethod
    def _acct_of(user, host):
        return f"{user.strip()}@{host.strip()}"

    def _acct(self):
        return self._acct_of(self.ssh_user_var.get(), self.ssh_host_var.get())

    @staticmethod
    def _kr_forget_acct(acct):
        for prefix in ("ssh", "keypass", "sudo"):
            kr_delete(f"{prefix}:{acct}")

    def _kr_store(self, prefix, acct, value):
        """Save value to keyring, or delete the entry if empty. Returns success."""
        if value:
            return kr_set(f"{prefix}:{acct}", value)
        kr_delete(f"{prefix}:{acct}")
        return True

    def _migrate_plaintext(self):
        """Move passwords left in plaintext by v9/v10 into the keyring and scrub the file."""
        c = self.cfg
        legacy = {"ssh_password": "ssh", "key_passphrase": "keypass", "sudo_password": "sudo"}
        present = [k for k in legacy if k in c]
        if not present or not self.kr_ok:
            return
        acct = self._acct_of(c.get("ssh_user", ""), c.get("ssh_host", ""))
        moved, ok = 0, True
        for k in present:
            val = c.get(k)
            if val and acct != "@":
                if kr_set(f"{legacy[k]}:{acct}", val):
                    moved += 1
                else:
                    ok = False
        if ok:
            for k in present:
                c.pop(k, None)
            save_config(c)
            if moved:
                self._startup_msg = (
                    f"✓ {moved} saved password(s) moved from the config file to the system keyring.",
                    SUCCESS)

    def _load_saved(self):
        c = self.cfg
        rem, rem_s = c.get("remember", False), c.get("remember_sudo", False)

        if rem:
            self.ssh_port_var.set(c.get("ssh_port", "22"))
            self.auth_var.set(c.get("auth_method", "password"))
            self.key_path_var.set(c.get("key_path", ""))
            self.nc_user_var.set(c.get("nc_user", ""))
            self.nc_data_var.set(c.get("nc_data", "/var/www/html/nextcloud/data"))
            self.nc_occ_var.set(c.get("nc_occ",  "/var/www/html/nextcloud/occ"))
            self.remote_var.set(c.get("remote",  "/"))
            self.tmp_var.set(c.get("tmp_dir", "/tmp"))
        else:
            self.ssh_port_var.set("22")
            self.nc_data_var.set("/var/www/html/nextcloud/data")
            self.nc_occ_var.set("/var/www/html/nextcloud/occ")
            self.remote_var.set("/")

        if rem or rem_s:
            self.ssh_host_var.set(c.get("ssh_host", ""))
            self.ssh_user_var.set(c.get("ssh_user", ""))
            acct = self._acct()
            self._kr_acct = acct
            if self.kr_ok:
                if rem:
                    self.ssh_pass_var.set(kr_get(f"ssh:{acct}"))
                    self.key_pass_var.set(kr_get(f"keypass:{acct}"))
                if rem_s:
                    self.sudo_pass_var.set(kr_get(f"sudo:{acct}"))

    def _no_keyring_warning(self, var):
        messagebox.showwarning(
            "No system keyring",
            "No system keyring is available on this computer, so passwords can't be "
            "stored safely. They will not be saved.")
        var.set(False)

    def _on_remember_toggle(self):
        if self.remember_var.get() and not self.kr_ok:
            self._no_keyring_warning(self.remember_var)
            return
        self._save_cfg()

    def _on_remember_sudo_toggle(self):
        if self.remember_sudo_var.get() and not self.kr_ok:
            self._no_keyring_warning(self.remember_sudo_var)
            return
        self._save_cfg()

    def _save_cfg(self):
        rem, rem_s = self.remember_var.get(), self.remember_sudo_var.get()
        acct = self._acct()
        ok = True

        # host/user changed -> drop entries stored under the old account name
        if self.kr_ok and self._kr_acct and self._kr_acct != acct:
            self._kr_forget_acct(self._kr_acct)
        self._kr_acct = acct

        data = {"remember": rem, "remember_sudo": rem_s}
        if rem or rem_s:
            data["ssh_host"] = self.ssh_host_var.get()
            data["ssh_user"] = self.ssh_user_var.get()
        if rem:
            data.update({
                "ssh_port":    self.ssh_port_var.get(),
                "auth_method": self.auth_var.get(),
                "key_path":    self.key_path_var.get(),
                "nc_user":     self.nc_user_var.get(),
                "nc_data":     self.nc_data_var.get(),
                "nc_occ":      self.nc_occ_var.get(),
                "remote":      self.remote_var.get(),
                "tmp_dir":     self.tmp_var.get(),
            })
        if self.kr_ok:
            if rem:
                ok &= self._kr_store("ssh",     acct, self.ssh_pass_var.get())
                ok &= self._kr_store("keypass", acct, self.key_pass_var.get())
            else:
                kr_delete(f"ssh:{acct}")
                kr_delete(f"keypass:{acct}")
            if rem_s:
                ok &= self._kr_store("sudo", acct, self.sudo_pass_var.get())
            else:
                kr_delete(f"sudo:{acct}")
        save_config(data)   # never contains passwords
        if not ok:
            self.after(0, lambda: self._set_status(
                "⚠ Could not write to the system keyring – passwords not saved.", WARNING))
        return ok

    def _forget_saved(self):
        if not messagebox.askyesno(
                "Forget saved passwords",
                "Delete all saved passwords (SSH, key passphrase, sudo) from the system "
                "keyring and the config file?\n\nHost, user and paths stay in the form."):
            return
        for acct in {self._kr_acct, self._acct()}:
            if acct and acct != "@":
                self._kr_forget_acct(acct)
        self.remember_var.set(False)
        self.remember_sudo_var.set(False)
        self.ssh_pass_var.set("")
        self.key_pass_var.set("")
        self.sudo_pass_var.set("")
        self._kr_acct = ""
        save_config({"remember": False, "remember_sudo": False})
        self._set_status("✓ Saved passwords deleted.", SUCCESS)

    # ── SSH helpers ──────────────────────────────────────────────────────────

    def _load_pkey(self):
        import paramiko
        path = os.path.expanduser(self.key_path_var.get().strip())
        if not path:
            raise Exception("No private key file selected.")
        if not os.path.isfile(path):
            raise Exception(f"Key file not found: {path}")
        passphrase = self.key_pass_var.get() or None
        last_err = None
        for cls_name in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey"):
            cls = getattr(paramiko, cls_name, None)
            if cls is None:
                continue
            try:
                return cls.from_private_key_file(path, password=passphrase)
            except paramiko.PasswordRequiredException:
                raise Exception("Key is encrypted – enter the key passphrase.")
            except Exception as e:
                last_err = e
        raise Exception(
            f"Cannot load key ({last_err}). Check the passphrase and that this is a "
            f"private key in OpenSSH/PEM format (not .pub; PuTTY .ppk must be converted).")

    def _ask_on_main(self, func):
        """Runs func() (a dialog) in the GUI thread and returns its result.
        Safe to call from the worker threads (connection test, upload, cleanup)."""
        if threading.current_thread() is threading.main_thread():
            return func()
        box, done = {}, threading.Event()

        def run():
            try:
                box["r"] = func()
            except Exception as e:
                box["e"] = e
            finally:
                done.set()
        self.after(0, run)
        done.wait()
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def _verify_host_key(self, host, port, key):
        """Trust-on-first-use host key check. Raises if the key is not trusted - this happens
        BEFORE any password or key is sent to the server."""
        import paramiko
        name  = hostkey_name(host, port)
        ktype = key.get_name()
        fp    = host_fingerprint(key)
        hk = paramiko.HostKeys()
        try:
            hk.load(KNOWN_HOSTS_FILE)
        except IOError:
            pass                                   # no file yet
        entry = hk.lookup(name)
        if entry is not None and entry.get(ktype) == key:
            return                                 # known server, same key

        if entry is None:
            ask = lambda: messagebox.askyesno(
                "New server - verify host key",
                f"First connection to {name}.\n\n"
                f"Server key ({ktype}):\n{fp}\n\n"
                "Make sure this is YOUR server: compare the fingerprint with the one on the server, e.g.\n"
                "  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub\n\n"
                "Trust this server and remember it?",
                icon="question", default="no", parent=self)
        else:
            old = "\n".join(f"  {k}: {host_fingerprint(v)}" for k, v in entry.items())
            ask = lambda: messagebox.askyesno(
                "WARNING - host key changed!",
                f"The key of {name} is NOT the one saved earlier.\n\n"
                f"Saved:\n{old}\n\nNow ({ktype}):\n  {fp}\n\n"
                "This can mean that someone is intercepting the connection (your passwords "
                "would be stolen), or that the server was reinstalled / its keys were changed.\n\n"
                "Trust the NEW key? Choose No unless you know why it changed.",
                icon="warning", default="no", parent=self)
        if not self._ask_on_main(ask):
            raise Exception("Server host key was not trusted - connection stopped "
                            "(no password was sent).")
        if entry is not None:
            del hk[name]                           # replace the old key
        hk.add(name, ktype, key)
        hk.save(KNOWN_HOSTS_FILE)
        try:
            os.chmod(KNOWN_HOSTS_FILE, 0o600)
        except Exception:
            pass

    def _make_ssh(self):
        import paramiko
        use_key = self.auth_var.get() == "key"
        pkey = self._load_pkey() if use_key else None
        host = self.ssh_host_var.get().strip()
        port = int(self.ssh_port_var.get() or 22)
        user = self.ssh_user_var.get().strip()

        sock = socket.create_connection((host, port), timeout=15)
        t = paramiko.Transport(sock)
        t.window_size              = 64 * 1024 * 1024
        t.packetizer.REKEY_BYTES   = 2 ** 40
        t.packetizer.REKEY_PACKETS = 2 ** 40
        try:
            t.start_client(timeout=15)             # key exchange only - nothing secret sent yet
            self._verify_host_key(host, port, t.get_remote_server_key())
            if use_key:
                t.auth_publickey(user, pkey)
            else:
                t.auth_password(user, self.ssh_pass_var.get())
            if not t.is_authenticated():
                raise Exception("Authentication failed.")
        except Exception:
            try: t.close()
            except Exception: pass
            raise
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client._transport = t
        return client

    def _tmp_root(self):
        return self.tmp_var.get().strip().rstrip("/") or "/tmp"

    def _remote_free_bytes(self, ssh, path):
        try:
            out, _, _ = self._ssh_run(ssh, f"df -Pk '{q(path)}' | tail -1", timeout=15)
            return int(out.split()[3]) * 1024
        except Exception:
            return None

    def _ssh_run(self, ssh, cmd, timeout=60):
        _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        code = stdout.channel.recv_exit_status()
        return stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace"), code

    def _sudo_run(self, ssh, cmd, timeout=120):
        sudo_pass        = self.sudo_pass_var.get()
        chan             = ssh.get_transport().open_session()
        self._active_chan = chan
        chan.get_pty()
        chan.exec_command(cmd)

        buf        = b""
        pwd_sent   = False
        t_prompt   = time.time() + 8
        t_deadline = time.time() + timeout

        while True:
            if self.cancel_flag.is_set():
                try: chan.close()
                except Exception: pass
                self._active_chan = None
                return buf.decode(errors="replace"), "cancelled", -1

            if time.time() > t_deadline:
                try: chan.close()
                except Exception: pass
                self._active_chan = None
                return buf.decode(errors="replace"), "timeout", 1

            if chan.recv_ready():
                chunk = chan.recv(65536)
                buf  += chunk
                if not pwd_sent and (b"password" in buf.lower() or
                                     (b": " in buf and len(buf) < 400)):
                    chan.send(sudo_pass + "\n")
                    pwd_sent = True
                    buf = b""
            else:
                if not pwd_sent and time.time() > t_prompt:
                    pwd_sent = True
                if chan.exit_status_ready():
                    while chan.recv_ready():
                        buf += chan.recv(65536)
                    break
                time.sleep(0.05)

        code = chan.recv_exit_status()
        try: chan.close()
        except Exception: pass
        self._active_chan = None
        return buf.decode(errors="replace"), "", code

    # ── Connection test ──────────────────────────────────────────────────────

    def _test_connection(self):
        self._set_status("Testing connection…", FG2)
        threading.Thread(target=self._do_test, daemon=True).start()

    def _do_test(self):
        try:
            ssh = self._make_ssh()
            out, _, _ = self._ssh_run(ssh, "echo PING", timeout=10)
            if "PING" not in out:
                self.after(0, lambda: self._set_status(
                    f"Unexpected SSH response: {out[:60]}", WARNING))
                ssh.close(); return
            if self.sudo_pass_var.get():
                out2, _, _ = self._sudo_run(ssh, "sudo -k echo SUDO_OK", timeout=15)
                ssh.close()
                if "SUDO_OK" in out2:
                    self.after(0, lambda: self._set_status(
                        "✓ SSH OK  |  ✓ sudo password correct.", SUCCESS))
                else:
                    self.after(0, lambda: self._set_status(
                        "✓ SSH OK  |  ✗ sudo password incorrect!", ERROR))
            else:
                ssh.close()
                self.after(0, lambda: self._set_status(
                    "✓ SSH OK. (sudo password not provided)", SUCCESS))
            self._save_cfg()
        except Exception as e:
            msg = str(e)
            self.after(0, lambda m=msg: self._set_status(f"SSH Error: {m}", ERROR))

    # ── File picking ─────────────────────────────────────────────────────────

    def _pick_files(self):
        paths = filedialog.askopenfilenames(title="Select files")
        if paths:
            self._add_paths(list(paths))

    def _pick_dir(self):
        path = filedialog.askdirectory(title="Select folder")
        if path:
            self._add_paths([path])

    def _set_paths(self, paths):
        self.selected_paths = []
        self._add_paths(paths)

    def _add_paths(self, paths):
        known = {os.path.normcase(os.path.normpath(p)) for p in self.selected_paths}
        for p in paths:
            n = os.path.normpath(p)
            if os.path.exists(n) and os.path.normcase(n) not in known:
                self.selected_paths.append(n)
                known.add(os.path.normcase(n))
        self._refresh_paths()

    def _refresh_paths(self):
        self.file_list.delete(0, "end")
        for p in self.selected_paths:
            self.file_list.insert("end", p)
        n = len(self.selected_paths)
        self.local_var.set("" if n == 0 else (self.selected_paths[0] if n == 1 else f"{n} items"))

    def _clear_paths(self):
        self.selected_paths = []
        self._refresh_paths()

    def _setup_dnd(self):
        if not DND_OK:
            return
        try:
            for w in (self, self.file_list):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        if str(self.btn_send["state"]) == "disabled":
            return event.action       # ignore drops during upload
        self._add_paths(list(self.tk.splitlist(event.data)))
        return event.action

    def _collect_files(self):
        result = []
        for p in self.selected_paths:
            p = os.path.normpath(p)
            if os.path.isdir(p):
                parent = os.path.dirname(p)
                for root, dirs, files in os.walk(p):
                    dirs.sort()
                    for fname in sorted(files):
                        full = os.path.join(root, fname)
                        rel  = os.path.relpath(full, parent).replace("\\", "/")
                        result.append((full, rel))
            elif os.path.isfile(p):
                result.append((p, os.path.basename(p)))
        return result

    # ── Cleanup ──────────────────────────────────────────────────────────────

    def _start_cleanup(self):
        if not self.ssh_host_var.get().strip():
            messagebox.showwarning("Missing host", "Enter the SSH server address.")
            return
        if self.auth_var.get() == "key" and not self.key_path_var.get().strip():
            messagebox.showwarning("Missing key", "Select the private key file.")
            return
        if not self.sudo_pass_var.get():
            messagebox.showwarning("Missing sudo password", "Enter the sudo password.")
            return
        self.btn_cleanup.configure(state="disabled")
        self.btn_send.configure(state="disabled")
        self._set_status("Cleaning tmp on server…", WARNING)
        threading.Thread(target=self._do_cleanup, daemon=True).start()

    def _do_cleanup(self):
        try:
            ssh = self._make_ssh()
            root = self._tmp_root()
            out, _, _ = self._ssh_run(
                ssh, f"ls '{q(root)}'/.nc_upload_* 2>/dev/null && echo EXISTS || echo EMPTY",
                timeout=10)
            if "EMPTY" in out:
                ssh.close()
                self.after(0, lambda: self._set_status("Tmp folder is already empty.", SUCCESS))
                self.after(0, self._reset_buttons)
                return
            _, err, code = self._ssh_run(
                ssh, f"rm -rf '{q(root)}'/.nc_upload_*", timeout=60)
            ssh.close()
            if code == 0:
                self.after(0, lambda: self._set_status("✓ Tmp folder cleaned.", SUCCESS))
            else:
                self.after(0, lambda e=err: self._set_status(
                    f"Cleanup error: {e[:100]}", ERROR))
        except Exception as e:
            msg = str(e)
            self.after(0, lambda m=msg: self._set_status(f"SSH Error: {m}", ERROR))
        finally:
            self.after(0, self._reset_buttons)

    # ── Upload ───────────────────────────────────────────────────────────────

    def _start_upload(self):
        if not self.selected_paths:
            messagebox.showwarning("No files", "Select a file or folder.")
            return
        if not self.ssh_host_var.get().strip():
            messagebox.showwarning("Missing host", "Enter the SSH server address.")
            return
        if self.auth_var.get() == "key" and not self.key_path_var.get().strip():
            messagebox.showwarning("Missing key", "Select the private key file.")
            return
        if not self.nc_user_var.get().strip():
            messagebox.showwarning("Missing NC user", "Enter the Nextcloud username.")
            return
        if not self.sudo_pass_var.get():
            messagebox.showwarning("Missing sudo password",
                                   "sudo password is required for mv/chown on the server.")
            return
        self.cancel_flag.clear()
        self.btn_send.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.btn_cleanup.configure(state="disabled")
        self.progress_var.set(0)
        self.speed_var.set("")
        self._save_cfg()
        threading.Thread(target=self._do_upload, daemon=True).start()

    def _cancel(self):
        self.cancel_flag.set()
        self._set_status("Cancelling…", WARNING)
        chan = getattr(self, "_active_chan", None)
        if chan:
            try: chan.close()
            except Exception: pass

    def _dest_base(self):
        data   = self.nc_data_var.get().rstrip("/")
        nc_usr = self.nc_user_var.get().strip()
        remote = self.remote_var.get().strip("/")
        base   = f"{data}/{nc_usr}/files"
        return f"{base}/{remote}" if remote else base

    def _do_upload(self):
        all_files = self._collect_files()
        if not all_files:
            self.after(0, lambda: self._set_status("No files found to send.", WARNING))
            self.after(0, self._reset_buttons)
            return

        total        = len(all_files)
        errors       = []
        total_bytes  = sum(os.path.getsize(f) for f, _ in all_files)
        sent_bytes   = [0]
        dest_base    = self._dest_base()

        # ── Connect SSH ──
        try:
            ssh = self._make_ssh()
        except Exception as e:
            self.after(0, lambda m=str(e): self._set_status(f"SSH connect error: {m}", ERROR))
            self.after(0, self._reset_buttons)
            return

        # tmp in /tmp – same filesystem as NC data → sudo mv = instant kernel rename
        tmp_tag  = secrets.token_hex(8)
        tmp_root = self._tmp_root()
        tmp_base = f"{tmp_root}/.nc_upload_{tmp_tag}"
        _, err_mk, code_mk = self._ssh_run(
            ssh, f"mkdir -p '{q(tmp_base)}' && chmod 700 '{q(tmp_base)}'", timeout=15)
        if code_mk != 0:
            self.after(0, lambda e=err_mk: self._set_status(
                f"Cannot create tmp dir: {e[:120]}", ERROR))
            try: ssh.close()
            except Exception: pass
            self.after(0, self._reset_buttons)
            return

        # ── Pre-flight: enough free space in the temp dir? ──
        free = self._remote_free_bytes(ssh, tmp_base)
        log_line(f"START files={total} bytes={total_bytes} tmp={tmp_base} "
                 f"free={free if free is not None else 'unknown'}")
        if free is not None and free < total_bytes + 64 * 1024 * 1024:
            msg = (f"Not enough free space in {tmp_root}: need {human_size(total_bytes)}, "
                   f"free {human_size(free)}. Change 'Server temp dir' to a bigger disk.")
            log_line("ABORT " + msg)
            self._ssh_run(ssh, f"rm -rf '{q(tmp_base)}'", timeout=30)
            try: ssh.close()
            except Exception: pass
            self.after(0, lambda m=msg: self._set_status(m, ERROR))
            self.after(0, self._reset_buttons)
            return

        # ── Phase 1: SFTP upload to temp dir ──
        tmp_map      = {}
        ok_sftp      = []
        t_start      = time.time()
        speed_window = []

        for i, (local_path, rel_path) in enumerate(all_files):
            if self.cancel_flag.is_set():
                break

            fname    = os.path.basename(local_path)
            tmp_name = secrets.token_hex(8) + "_" + fname.replace("/", "_")
            tmp_path = f"{tmp_base}/{tmp_name}"
            tmp_map[rel_path] = tmp_path

            file_size = os.path.getsize(local_path)
            file_sent = [0]
            last_ui   = [0.0]

            def make_callback(file_idx, file_total_bytes):
                def callback(transferred, total_f):
                    if self.cancel_flag.is_set():
                        return
                    delta = transferred - file_sent[0]
                    if delta > 0:
                        file_sent[0]   = transferred
                        sent_bytes[0] += delta

                    now = time.time()
                    if now - last_ui[0] < 0.2 and transferred < total_f:
                        return
                    last_ui[0] = now

                    speed_window.append((now, sent_bytes[0]))
                    speed_window[:] = [(t2, b) for t2, b in speed_window if now - t2 <= 3.0]
                    if len(speed_window) >= 2:
                        dt    = speed_window[-1][0] - speed_window[0][0]
                        db    = speed_window[-1][1] - speed_window[0][1]
                        speed = db / dt if dt > 0.001 else 0
                    else:
                        speed = sent_bytes[0] / max(now - t_start, 0.01)

                    pct  = min(sent_bytes[0] / max(total_bytes, 1) * 100, 100)
                    sp_s = f"{human_size(speed)}/s" if speed > 0 else "..."
                    rem  = total_bytes - sent_bytes[0]
                    eta  = int(rem / speed) if speed > 100 else 0
                    if eta >= 3600:   eta_s = f"{eta//3600}h {(eta%3600)//60}m"
                    elif eta >= 60:   eta_s = f"{eta//60}m {eta%60}s"
                    elif eta > 0:     eta_s = f"{eta}s"
                    else:             eta_s = "-"

                    label = (f"[{file_idx+1}/{total}] {fname}  "
                             f"{int(pct)}%  "
                             f"{human_size(sent_bytes[0])}/{human_size(total_bytes)}")
                    info = f"{sp_s}  ETA {eta_s}"
                    self.after(0, lambda p=pct:    self.progress_var.set(p))
                    self.after(0, lambda s=label:  self._set_status(s, FG2))
                    self.after(0, lambda v=info:   self.speed_var.set(v))
                return callback

            # Fresh SFTP session per file – fixes Bad message / Socket closed on folders
            try:
                sftp = ssh.open_sftp()
                sftp.put(local_path, tmp_path, callback=make_callback(i, file_size))
                sftp.close()
                if not self.cancel_flag.is_set():
                    ok_sftp.append(rel_path)
            except Exception as e:
                import traceback
                err_msg = (f"{fname}: {e} (failed after {human_size(file_sent[0])} "
                           f"of {human_size(file_size)})")
                free_now = self._remote_free_bytes(ssh, tmp_base)
                if free_now is not None:
                    err_msg += f", free in {tmp_root}: {human_size(free_now)}"
                log_line("UPLOAD ERROR " + err_msg + "\n" + traceback.format_exc())
                errors.append(err_msg)
                self.after(0, lambda m=err_msg: self._set_status(f"Upload error: {m}", ERROR))
                try:
                    sftp2 = ssh.open_sftp(); sftp2.remove(tmp_path); sftp2.close()
                except Exception:
                    pass

        if self.cancel_flag.is_set() or not ok_sftp:
            self._ssh_run(ssh, f"rm -rf '{q(tmp_base)}'", timeout=30)
            try: ssh.close()
            except Exception: pass
            self.after(0, lambda: self.speed_var.set(""))
            if self.cancel_flag.is_set():
                self.after(0, lambda: self._set_status("Cancelled.", WARNING))
                self.after(0, lambda: self.progress_var.set(0))
            else:
                all_err = " | ".join(errors[:3]) if errors else "Unknown SFTP error"
                self.after(0, lambda m=all_err: self._set_status(f"Upload failed: {m}  (log: ~/NCSender_upload.log)", ERROR))
            self.after(0, self._reset_buttons)
            return

        # ── Phase 2: sudo bash script – mkdir + mv + chown ──
        self.after(0, lambda: self.progress_var.set(100))
        self.after(0, lambda: self.speed_var.set(""))
        self.after(0, lambda n=len(ok_sftp): self._set_status(
            f"[1/3] Moving {n} file(s) to Nextcloud…", FG2))

        nc_user   = self.nc_user_var.get().strip()
        occ       = self.nc_occ_var.get().strip()
        remote    = self.remote_var.get().strip("/")
        scan_path = (f"--path=/{nc_user}/files/{remote}"
                     if remote else f"--path=/{nc_user}/files")

        script_lines = ["#!/bin/bash", "set -e"]
        for rel_path in ok_sftp:
            src     = tmp_map[rel_path]
            dst     = f"{dest_base}/{rel_path}"
            dst_dir = dst.rsplit("/", 1)[0]
            script_lines += [
                f"mkdir -p '{q(dst_dir)}'",
                f"chown www-data:www-data '{q(dst_dir)}'",
                f"mv '{q(src)}' '{q(dst)}'",
                f"chown www-data:www-data '{q(dst)}'",
                f"chmod 640 '{q(dst)}'",
            ]
        script_lines += [
            f"rm -rf '{q(tmp_base)}'",
            f"echo NC_MV_DONE",
        ]

        tag         = secrets.token_hex(6)
        script_path = f"/tmp/.nc_mv_{tag}.sh"
        script_body = "\n".join(script_lines) + "\n"

        try:
            sftp2 = ssh.open_sftp()
            with sftp2.open(script_path, "wb") as f:
                f.write(script_body.encode("utf-8"))
            sftp2.chmod(script_path, 0o700)
            sftp2.close()
        except Exception as e:
            self._ssh_run(ssh, f"rm -rf '{q(tmp_base)}'", timeout=30)
            try: ssh.close()
            except Exception: pass
            self.after(0, lambda m=str(e): self._set_status(f"Script write error: {m}", ERROR))
            self.after(0, self._reset_buttons)
            return

        out, err, code = self._sudo_run(
            ssh, f"sudo bash '{q(script_path)}'", timeout=600)
        self._ssh_run(ssh, f"rm -f '{q(script_path)}'", timeout=10)

        if self.cancel_flag.is_set():
            try: ssh.close()
            except Exception: pass
            self.after(0, lambda: self._set_status("Cancelled during server move.", WARNING))
            self.after(0, lambda: self.progress_var.set(0))
            self.after(0, self._reset_buttons)
            return

        if "NC_MV_DONE" not in out:
            err_detail = ((err or "") + (out or "")).strip()[-400:]
            errors.append(f"Server mv error: {err_detail}")
            log_line(f"MV ERROR {err_detail}")
            try: ssh.close()
            except Exception: pass
            self.after(0, lambda: self._finish(0, total, errors))
            return

        # ── Phase 3: files:scan ──
        self.after(0, lambda: self._set_status(
            f"[2/3] Registering files in Nextcloud DB (files:scan)…", FG2))

        out_scan, err_scan, code_scan = self._sudo_run(
            ssh,
            f"sudo -u www-data php '{q(occ)}' files:scan {scan_path} --quiet && echo NC_SCAN_DONE",
            timeout=600)

        try: ssh.close()
        except Exception: pass

        if self.cancel_flag.is_set():
            self.after(0, lambda: self._set_status("Cancelled during scan.", WARNING))
            self.after(0, lambda: self.progress_var.set(0))
            self.after(0, self._reset_buttons)
            return

        if "NC_SCAN_DONE" not in out_scan:
            errors.append(f"files:scan error: {(err_scan + out_scan).strip()[-200:]}")
            log_line(f"SCAN ERROR {(err_scan + out_scan).strip()[-500:]}")

        self.after(0, lambda n=len(ok_sftp): self._finish(n, total, errors))

    def _finish(self, ok, total, errors):
        self.speed_var.set("")
        self._reset_buttons()
        if errors:
            self._set_status(
                f"⚠ {ok}/{total} sent. Error: {' | '.join(errors[:2])}", WARNING)
        else:
            self._set_status(f"✓ Done! Sent {ok} file(s). Nextcloud updated.", SUCCESS)


class NextcloudSFTPUploader(_Uploader, _TkBase):
    pass


class NextcloudSFTPUploaderPlain(_Uploader, tk.Tk):
    pass


def write_dnd_log(e):
    import traceback
    lines = ["".join(traceback.format_exception(type(e), e, e.__traceback__))]
    try:
        import tkinterdnd2
        base = os.path.join(os.path.dirname(os.path.abspath(tkinterdnd2.__file__)), "tkdnd")
        lines.append(f"tkinterdnd2 file: {tkinterdnd2.__file__}")
        lines.append(f"tkdnd dir: {base}  exists={os.path.isdir(base)}")
        for root, _, files in os.walk(base):
            for f in sorted(files):
                lines.append(os.path.join(root, f))
    except Exception as ex:
        lines.append(f"listing failed: {ex}")
    try:
        with open(os.path.join(os.path.expanduser("~"), "NCSender_dnd.log"),
                  "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


def report_crash():
    import traceback
    tb  = traceback.format_exc()
    log = os.path.join(os.path.expanduser("~"), "NCSender_error.log")
    try:
        with open(log, "w", encoding="utf-8") as f:
            f.write(tb)
    except Exception:
        pass
    try:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("Startup error", f"{tb[-1500:]}\n\n(saved to {log})")
    except Exception:
        pass


def main():
    global DND_OK, DND_ERR
    install_if_missing("paramiko")
    try:
        install_if_missing("keyring")
    except Exception:
        pass
    try:
        app = NextcloudSFTPUploader()
    except Exception as e:
        if not DND_OK:
            raise
        DND_OK  = False          # tkdnd failed to load -> run without drag & drop
        cause   = e.__context__ or e.__cause__
        DND_ERR = (f"{e} | cause: {cause}" if cause else str(e))[:400]
        write_dnd_log(e)
        # destroy the half-created root window left behind by the failed attempt
        try:
            orphan = getattr(tk, "_default_root", None)
            if orphan is not None:
                orphan.destroy()
        except Exception:
            pass
        tk._default_root = None
        app = NextcloudSFTPUploaderPlain()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        report_crash()