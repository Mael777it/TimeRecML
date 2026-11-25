"""TimeRecML - maly pasek na pulpicie do rozpisywania czynnosci w pracy.

Enter         -> dopisz czynnosc (bez timera)
Ctrl+Enter    -> start timera dla czynnosci (poprzedni timer sie zatrzymuje)
"45m opis" / "1h30 opis"      -> czynnosc z czasem
Ctrl+Alt+T (globalnie)        -> fokus na pasku
Dane: dane.json obok tego pliku.
"""
import csv
import ctypes
import ctypes.wintypes
import datetime as dt
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(APP_DIR, "dane.json")
ICON = os.path.join(APP_DIR, "TimeRecML.ico")
APP = "TimeRecML"
REC = "#e01b24"
BAND, CHILD = "#2a2f3a", "#1a1d24"  # tlo naglowka katalogu / czynnosci w katalogu
BAR_W = 660  # szerokosc paska (i listy dnia)

BG, BG2, FG, MUTED, LINE = "#1d2026", "#14161a", "#e6e8eb", "#9aa1ab", "#3a3f48"
GREEN, RED, WARN, ACC = "#4ade80", "#f87171", "#f5b04a", "#6c9bff"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")
MONO = ("Consolas", 11, "bold")
PLACEHOLDER = "co robisz? Enter · Ctrl+Enter = timer"

RE_RANGE = re.compile(r"^(\d{1,2})[:.](\d{2})\s*[-–]\s*(\d{1,2})[:.](\d{2})\s+(.+)$")
RE_HM = re.compile(r"^(\d+)\s*h(?:\s*(\d+)\s*(?:m|min)?)?\s+(.+)$", re.I)
RE_M = re.compile(r"^(\d+)\s*(?:m|min)\s+(.+)$", re.I)


# ---------------------------------------------------------------- helpers
def now():
    return time.time()


def ymd(d):
    return d.strftime("%Y-%m-%d")


def hhmm(t):
    return dt.datetime.fromtimestamp(t).strftime("%H:%M")


def at_time(day, txt):
    m = re.fullmatch(r"\s*(\d{1,2})[:.]?(\d{2})\s*", txt or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return dt.datetime.combine(day, dt.time(int(m.group(1)), int(m.group(2)))).timestamp()


def fmt_dur(sec):
    m = int(round(sec / 60))
    h, r = divmod(m, 60)
    return f"{h}h {r:02d}m" if h else f"{r}m"


def fmt_clock(sec):
    s = int(sec)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def parse_mins(txt):
    """'45', '45m', '1h30', '1:30', '1.5h' -> minuty albo None"""
    t = (txt or "").strip().lower().replace(",", ".")
    if not t:
        return None
    m = re.fullmatch(r"(\d+):(\d{2})", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+)\s*(?:m|min)?)?", t)
    if m and (m.group(1) or m.group(2)):
        return int(round(float(m.group(1) or 0) * 60)) + int(m.group(2) or 0)
    return None


# ---------------------------------------------------------------- data
class Store:
    """Wpis: id, desc, day (YYYY-MM-DD), created, start, end, mins, run"""

    def __init__(self):
        self.entries = []
        self.folders = []  # katalogi na dany dzien: id, name, day, created
        self.settings = {"rounding": 15, "x": None, "y": None}
        try:
            with open(DATA, encoding="utf-8") as f:
                d = json.load(f)
            self.entries = d.get("entries", [])
            self.folders = d.get("folders", [])
            self.settings.update(d.get("settings", {}))
        except FileNotFoundError:
            pass
        except Exception as ex:  # uszkodzony plik - nie nadpisuj go
            bak = DATA + f".uszkodzony-{int(now())}"
            os.replace(DATA, bak)
            messagebox.showwarning(APP, f"Nie udało się odczytać danych ({ex}).\nPlik przeniesiono do:\n{bak}")

    def save(self):
        tmp = DATA + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"entries": self.entries, "folders": self.folders, "settings": self.settings},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, DATA)

    # -- queries
    def running(self):
        return next((e for e in self.entries if e.get("run")), None)

    @staticmethod
    def dur(e):
        if e.get("run"):
            return max(0, now() - e["start"])
        if e.get("mins") is not None:  # recznie ustawiony czas ma pierwszenstwo
            return e["mins"] * 60
        if e.get("start") is not None and e.get("end") is not None:
            return max(0, e["end"] - e["start"])
        return (e.get("mins") or 0) * 60

    @staticmethod
    def key(e):
        return e["start"] if e.get("start") is not None else e["created"]

    def day(self, d):
        k = ymd(d)
        return sorted([e for e in self.entries if e["day"] == k], key=self.key)

    def between(self, d1, d2):
        a, b = ymd(d1), ymd(d2)
        return sorted([e for e in self.entries if a <= e["day"] < b], key=lambda e: (e["day"], self.key(e)))

    def recent(self, n=200):
        seen = {}
        for e in sorted(self.entries, key=lambda e: e["created"], reverse=True):
            seen.setdefault(e["desc"].strip().lower(), e["desc"].strip())
        return list(seen.values())[:n]

    # -- changes
    def _new(self, desc, day, **kw):
        e = {"id": uuid.uuid4().hex[:10], "desc": desc.strip(), "day": ymd(day), "created": now(),
             "start": None, "end": None, "mins": None, "run": False}
        e.update(kw)
        self.entries.append(e)
        return e

    def stop(self, at=None):
        r = self.running()
        if r:
            r["run"] = False
            r["end"] = max(r["start"] + 1, at or now())

    def add_plain(self, desc, day):
        return self._new(desc, day)

    def add_mins(self, desc, day, mins):
        return self._new(desc, day, mins=mins)

    def add_range(self, desc, day, s, e):
        if e <= s:
            e += 86400
        return self._new(desc, day, start=s, end=e)

    def start_timer(self, desc):
        self.stop()
        return self._new(desc, dt.date.today(), start=now(), run=True)

    def remove(self, e):
        self.entries = [x for x in self.entries if x is not e]

    # -- katalogi
    def day_folders(self, d):
        k = ymd(d)
        return sorted([f for f in self.folders if f["day"] == k], key=lambda f: f["created"])

    def folder(self, fid):
        return next((f for f in self.folders if f["id"] == fid), None)

    def add_folder(self, name, day):
        f = {"id": uuid.uuid4().hex[:10], "name": name.strip(), "day": ymd(day), "created": now()}
        self.folders.append(f)
        return f

    def remove_folder(self, f):
        for e in self.entries:
            if e.get("folder") == f["id"]:
                e["folder"] = None
        self.folders = [x for x in self.folders if x is not f]

    def folder_names(self):
        seen = {}
        for f in sorted(self.folders, key=lambda f: f["created"], reverse=True):
            seen.setdefault(f["name"].lower(), f["name"])
        return list(seen.values())[:50]


def round_sec(sec, step):
    if not step or sec <= 0:
        return sec
    m = int(round(sec / 60))  # pelne minuty, zeby 15:00 nie stalo sie 30m
    return max(step, -(-m // step) * step) * 60  # zawsze w gore


def groups(entries, step):
    g = {}
    for e in entries:
        k = e["desc"].strip().lower()
        o = g.setdefault(k, {"desc": e["desc"].strip(), "sec": 0, "n": 0})
        o["sec"] += Store.dur(e)
        o["n"] += 1
    out = sorted(g.values(), key=lambda o: (-o["sec"], o["desc"].lower()))
    for o in out:
        o["sec"] = round_sec(o["sec"], step)
    return out


# ---------------------------------------------------------------- windows helpers
def work_area():
    try:
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def hotkey_thread(q):
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, 0x0002 | 0x0001 | 0x4000, ord("T")):  # Ctrl+Alt+T
        return
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:
            q.put("focus")


# ---------------------------------------------------------------- app
class App:
    def __init__(self):
        self.s = Store()
        self.sel = dt.date.today()
        self.q = queue.Queue()
        self.flash_until = 0

        r = self.root = tk.Tk()
        r.title(APP)
        try:
            r.iconbitmap(default=ICON)  # ikona dla wszystkich okien (lista, dialogi)
        except tk.TclError:
            pass
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=LINE)
        self._style()

        bar = tk.Frame(r, bg=BG)
        bar.pack(padx=1, pady=1, fill="both", expand=True)
        self.bar = bar
        grip = tk.Label(bar, text="⠿", bg=BG, fg=MUTED, cursor="fleur", font=("Segoe UI", 12))
        grip.pack(side="left", padx=(6, 2))
        self.rec = tk.Label(bar, text="●", bg=BG, fg=LINE, font=("Segoe UI", 16), cursor="hand2")
        self.rec.pack(side="left", padx=(2, 0))
        self.clock = tk.Label(bar, text="", bg=BG, fg=MUTED, font=MONO, cursor="hand2")
        self.clock.pack(side="left", padx=(2, 4))
        self.cur = tk.Label(bar, text="", bg=BG, fg=FG, font=FONT, anchor="w", cursor="hand2")
        self.cur.pack(side="left")
        # przyciski od prawej, pole tekstowe wypelnia cala reszte paska
        self._btn(bar, "×", MUTED, self.quit, "Zamknij")
        self._btn(bar, "▤", FG, self.toggle_panel, "Lista dnia")
        self.btn_stop = self._btn(bar, "■", RED, self.stop, "Zatrzymaj nagrywanie")
        self.btn_play = self._btn(bar, "●", REC, lambda: self.submit(timer=True), "Nagrywaj czas (Ctrl+Enter)", size=18)
        self.entry = tk.Entry(bar, bg=BG2, fg=FG, insertbackground=FG, relief="flat", font=FONT, width=10,
                              highlightthickness=1, highlightbackground=LINE, highlightcolor=ACC)
        self.entry.pack(side="left", fill="x", expand=True, padx=(6, 4), pady=4, ipady=3)

        for w in (grip, self.rec, self.clock, self.cur):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
        self.entry.bind("<Return>", lambda e: (self._ac_pick() or self.submit(timer=False), "break")[1])
        self.entry.bind("<Control-Return>", lambda e: (self._ac_pick() or self.submit(timer=True), "break")[1])
        self.entry.bind("<Escape>", self._esc)
        self.entry.bind("<KeyRelease>", self._ac_update)
        self.entry.bind("<Down>", lambda e: self._ac_move(1))
        self.entry.bind("<Up>", lambda e: self._ac_move(-1))
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Button-1>", lambda e: self.entry.focus_force())
        self._placeholder(True)

        # autouzupelnianie
        self.ac = tk.Toplevel(r)
        self.ac.overrideredirect(True)
        self.ac.attributes("-topmost", True)
        self.ac.withdraw()
        self.lb = tk.Listbox(self.ac, bg=BG2, fg=FG, selectbackground=ACC, selectforeground="#fff", font=FONT,
                             relief="flat", highlightthickness=1, highlightbackground=LINE, activestyle="none", height=8)
        self.lb.pack(fill="both", expand=True)
        self.lb.bind("<ButtonRelease-1>", lambda e: (self._ac_pick(), self.entry.focus_force()))

        self.panel = None
        r.update_idletasks()
        self._place_bar()
        threading.Thread(target=hotkey_thread, args=(self.q,), daemon=True).start()
        self._tick()
        self._poll()
        r.protocol("WM_DELETE_WINDOW", self.quit)

    # ---------- ui bits
    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=BG2, font=FONT, bordercolor=LINE,
                     lightcolor=LINE, darkcolor=LINE, troughcolor=BG2, focuscolor=ACC)
        st.configure("Treeview", background=BG2, fieldbackground=BG2, foreground=FG, rowheight=24, borderwidth=0)
        st.configure("Treeview.Heading", background=BG, foreground=MUTED, relief="flat", font=("Segoe UI", 9))
        st.map("Treeview", background=[("selected", "#2b3a5c")], foreground=[("selected", "#fff")])
        st.configure("TButton", background=BG2, foreground=FG, padding=(8, 3), relief="flat")
        st.map("TButton", background=[("active", "#2b3a5c")])
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("TEntry", fieldbackground=BG2, foreground=FG, insertcolor=FG)
        st.configure("TCombobox", fieldbackground=BG2, background=BG2, foreground=FG, arrowcolor=FG,
                     selectbackground=BG2, selectforeground=FG, padding=(6, 3))
        st.map("TCombobox", fieldbackground=[("readonly", BG2)], foreground=[("readonly", FG)],
               background=[("active", "#2b3a5c"), ("readonly", BG2)], arrowcolor=[("readonly", FG)],
               bordercolor=[("focus", LINE), ("!focus", LINE)], lightcolor=[("focus", LINE)], darkcolor=[("focus", LINE)],
               selectbackground=[("readonly", BG2)], selectforeground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", BG2)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACC)
        self.root.option_add("*TCombobox*Listbox.font", FONT)
        st.map("TMenubutton", background=[("active", "#2b3a5c")])
        st.configure("Folder.Treeview", font=FONT_B)
        st.configure("TMenubutton", background=BG2, foreground=FG, padding=(8, 3), relief="flat", arrowcolor=FG)
        st.configure("Vertical.TScrollbar", background=BG, troughcolor=BG2, arrowcolor=MUTED, bordercolor=BG)

    def _btn(self, parent, txt, color, cmd, tip, size=11):
        b = tk.Label(parent, text=txt, bg=BG, fg=color, font=("Segoe UI", size), cursor="hand2", padx=5, pady=0)
        b.pack(side="right", padx=1)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: (b.configure(bg=BG2), self._flash(tip, 0)))
        b.bind("<Leave>", lambda e: (b.configure(bg=BG), self._flash_end()))
        return b

    def _placeholder(self, on):
        self._ph = on
        self.entry.delete(0, "end")
        if on:
            self.entry.insert(0, PLACEHOLDER)
            self.entry.configure(fg=MUTED)
        else:
            self.entry.configure(fg=FG)

    def _focus_in(self, _e):
        if self._ph:
            self._placeholder(False)

    def _focus_out(self, _e):
        self.root.after(150, self._ac_hide)
        if not self.entry.get().strip():
            self._placeholder(True)

    def _esc(self, _e):
        if self.ac.winfo_viewable():
            self._ac_hide()
        else:
            self.entry.delete(0, "end")
            self.root.focus_set()
            self._placeholder(True)
        return "break"

    def text(self):
        return "" if self._ph else self.entry.get().strip()

    def _flash(self, msg, ms=1800):
        self.flash_until = now() + (ms / 1000 if ms else 3600)
        self.cur.configure(text=msg, fg=ACC)

    def _flash_end(self):
        self.flash_until = 0
        self._tick(once=True)

    # ---------- drag / position
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._moved = False
        self._p0 = (e.x_root, e.y_root)

    def _drag_move(self, e):
        if abs(e.x_root - self._p0[0]) + abs(e.y_root - self._p0[1]) > 3:
            self._moved = True
        if self._moved:
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
            if self.panel and self.panel.winfo_viewable():
                self._place_panel()

    def _drag_end(self, e):
        if self._moved:
            self.s.settings["x"], self.s.settings["y"] = self.root.winfo_x(), self.root.winfo_y()
            self.s.save()
        elif e.widget in (self.clock, self.cur):
            self.toggle_panel()

    def _place_bar(self):
        x, y = self.s.settings.get("x"), self.s.settings.get("y")
        w, h = BAR_W, self.root.winfo_reqheight()
        vw, vh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        if x is None or y is None or not (-vw < x < vw * 2 and -vh < y < vh * 2):
            wa = work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
            x, y = wa[2] - w - 16, wa[3] - h - 12
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- autocomplete
    def _ac_update(self, e):
        if e.keysym in ("Up", "Down", "Return", "Escape", "Control_L", "Control_R"):
            return
        t = self.text().lower()
        items = [d for d in self.s.recent() if t in d.lower() and d.lower() != t][:8] if t else []
        if not items:
            return self._ac_hide()
        self.lb.delete(0, "end")
        for d in items:
            self.lb.insert("end", d)
        self.lb.configure(height=len(items))
        self._ac_show()

    def _ac_show(self):
        self.ac.update_idletasks()
        x = self.entry.winfo_rootx()
        w = max(self.entry.winfo_width(), 320)
        h = self.lb.winfo_reqheight()
        y = self.root.winfo_y() + self.root.winfo_height()
        if y + h > self.root.winfo_screenheight() - 40:
            y = self.root.winfo_y() - h
        self.ac.geometry(f"{w}x{h}+{x}+{y}")
        self.ac.deiconify()
        self.ac.lift()

    def _ac_hide(self):
        self.ac.withdraw()
        self.lb.selection_clear(0, "end")

    def _ac_move(self, d):
        if not self.ac.winfo_viewable():
            items = self.s.recent(8)
            if not items:
                return "break"
            self.lb.delete(0, "end")
            for it in items:
                self.lb.insert("end", it)
            self.lb.configure(height=len(items))
            self._ac_show()
        n = self.lb.size()
        cur = self.lb.curselection()
        i = (cur[0] + d) % n if cur else (0 if d > 0 else n - 1)
        self.lb.selection_clear(0, "end")
        self.lb.selection_set(i)
        self.lb.see(i)
        return "break"

    def _ac_pick(self):
        """Jesli cos zaznaczone w podpowiedziach - wstaw do pola. Zwraca True gdy tylko wstawiono."""
        if self.ac.winfo_viewable() and self.lb.curselection():
            v = self.lb.get(self.lb.curselection()[0])
            self._placeholder(False)
            self.entry.insert(0, v)
            self._ac_hide()
            return True
        self._ac_hide()
        return False

    # ---------- actions
    def submit(self, timer=False):
        v = self.text()
        if not v:
            if timer:
                self._flash("najpierw wpisz czynność")
            return
        day = self.sel if (self.panel and self.panel.winfo_viewable()) else dt.date.today()
        today = day == dt.date.today()
        m = RE_RANGE.match(v)
        if m:
            s = at_time(day, f"{m.group(1)}:{m.group(2)}")
            e = at_time(day, f"{m.group(3)}:{m.group(4)}")
            if s is None or e is None:
                return self._flash("zła godzina")
            self.s.add_range(m.group(5), day, s, e)
            self._flash(f"✔ dopisano {fmt_dur((e - s) % 86400)}")
        elif (m := RE_HM.match(v)) or (m2 := RE_M.match(v)):
            if m:
                mins, desc = int(m.group(1)) * 60 + int(m.group(2) or 0), m.group(3)
            else:
                mins, desc = int(m2.group(1)), m2.group(2)
            self.s.add_mins(desc, day, mins)
            self._flash(f"✔ dopisano {fmt_dur(mins * 60)}")
        elif timer:
            self.s.start_timer(v)
            self.sel = dt.date.today()
        else:
            self.s.add_plain(v, day)
            self._flash("✔ dopisano" + ("" if today else f" ({day.strftime('%d.%m')})"))
        self.s.save()
        self.entry.delete(0, "end")
        self.refresh()

    def stop(self):
        if self.s.running():
            self.s.stop()
            self.s.save()
            self.refresh()
            self._flash("■ zatrzymano")

    def quit(self):
        if self.s.running():
            a = messagebox.askyesnocancel(APP, "Timer nadal liczy.\n\nTak – zatrzymaj i zamknij\n"
                                                        "Nie – zamknij, timer liczy dalej (np. do jutra)")
            if a is None:
                return
            if a:
                self.s.stop()
        self.s.settings["x"], self.s.settings["y"] = self.root.winfo_x(), self.root.winfo_y()
        self.s.save()
        self.root.destroy()

    def focus_input(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.entry.focus_force()

    # ---------- periodic
    def _poll(self):
        try:
            while True:
                if self.q.get_nowait() == "focus":
                    self.focus_input()
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _tick(self, once=False):
        r = self.s.running()
        if r:
            self.clock.configure(text=fmt_clock(Store.dur(r)), fg=FG)
            self.rec.configure(fg=REC if int(now()) % 2 == 0 or once else BG)  # migajaca kropka REC
            self.btn_stop.configure(fg=RED)
        else:
            tot = sum(o["sec"] for o in groups(self.s.day(dt.date.today()), self.s.settings.get("rounding", 15)))
            self.clock.configure(text=f"Σ {fmt_dur(tot)}", fg=MUTED)
            self.rec.configure(fg=LINE)
            self.btn_stop.configure(fg=LINE)
        if not self.flash_until or now() > self.flash_until:
            self.flash_until = 0
            if r:
                d = r["desc"]
                self.cur.configure(text=d if len(d) <= 26 else d[:25] + "…", fg=FG)
            else:
                n = len(self.s.day(dt.date.today()))
                self.cur.configure(text=f"{n} {'czynność' if n == 1 else 'czynności'}", fg=MUTED)
        if self.panel and self.panel.winfo_viewable() and r:
            if self.tree.exists(r["id"]):
                self.tree.set(r["id"], "czas", fmt_dur(Store.dur(r)))
            if int(now()) % 30 == 0:
                self._fill_summary()
        if not once:
            self.root.after(1000, self._tick)

    def refresh(self):
        self._tick(once=True)
        if self.panel and self.panel.winfo_viewable():
            self._fill_panel()

    # ---------- panel
    def toggle_panel(self):
        if self.panel and self.panel.winfo_viewable():
            self.panel.withdraw()
            self.sel = dt.date.today()
            return
        if not self.panel:
            self._build_panel()
        self.sel = dt.date.today()
        self._fill_panel()
        self.panel.deiconify()
        self._place_panel()
        self.panel.lift()

    def _place_panel(self):
        p = self.panel
        p.update_idletasks()
        bx, by = self.root.winfo_x(), self.root.winfo_y()
        w, bh = self.root.winfo_width(), self.root.winfo_height()
        h = p.winfo_height() if p.winfo_viewable() and p.winfo_height() > 200 else 380
        y = by - h - 32 if by > self.root.winfo_screenheight() / 2 else by + bh + 4
        p.geometry(f"{w}x{h}+{bx}+{max(0, y)}")

    def _build_panel(self):
        p = self.panel = tk.Toplevel(self.root)
        p.title(f"{APP} – dzień")
        p.attributes("-toolwindow", True)
        p.attributes("-topmost", True)
        p.configure(bg=BG)
        p.protocol("WM_DELETE_WINDOW", self.toggle_panel)
        p.minsize(420, 240)
        self._open = {}   # rozwiniecie katalogow
        self._dnd = None  # stan przeciagania

        top = ttk.Frame(p, padding=(8, 6, 8, 4))
        top.pack(fill="x")
        ttk.Button(top, text="◀", width=2, command=lambda: self._shift(-1)).pack(side="left")
        self.day_lbl = ttk.Label(top, text="", font=FONT_B, anchor="center")
        self.day_lbl.pack(side="left", padx=6)
        ttk.Button(top, text="▶", width=2, command=lambda: self._shift(1)).pack(side="left")
        ttk.Button(top, text="Dziś", width=5, command=lambda: self._shift(0)).pack(side="left", padx=4)
        ttk.Button(top, text="+", width=3, command=self._new_folder).pack(side="right", padx=(8, 0))  # nowy katalog
        self.tot_lbl = ttk.Label(top, text="", font=FONT_B)
        self.tot_lbl.pack(side="right")

        bot = ttk.Frame(p, padding=(8, 4, 8, 8))
        bot.pack(side="bottom", fill="x")
        ttk.Button(bot, text="Kopiuj sumę", command=self.copy_summary).pack(side="left")
        ttk.Button(bot, text="Kopiuj listę", command=self.copy_list).pack(side="left", padx=4)
        mb = ttk.Menubutton(bot, text="CSV")
        menu = tk.Menu(mb, tearoff=0, bg=BG2, fg=FG, activebackground=ACC)
        menu.add_command(label="Ten dzień", command=lambda: self.export("day"))
        menu.add_command(label="Ten tydzień", command=lambda: self.export("week"))
        menu.add_command(label="Ten miesiąc", command=lambda: self.export("month"))
        mb["menu"] = menu
        mb.pack(side="left")
        self.round_mb = ttk.Menubutton(bot, text="", width=9)
        rmenu = tk.Menu(self.round_mb, tearoff=0, bg=BG2, fg=FG, activebackground=ACC)
        for step in (0, 5, 15, 30):
            rmenu.add_command(label=self._round_label(step), command=lambda s=step: self._round_changed(s))
        self.round_mb["menu"] = rmenu
        self.round_mb.configure(text=self._round_label(self.s.settings.get("rounding", 15)))
        self.round_mb.pack(side="right")
        self.det_btn = ttk.Button(bot, text="", command=self._toggle_details)
        self.det_btn.pack(side="right", padx=4)

        self.hint = ttk.Label(p, text="", style="Muted.TLabel", padding=(8, 0))
        self.hint.pack(side="bottom", fill="x")

        mid = ttk.Frame(p, padding=(8, 0))
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=("godz", "czas", "opis"), selectmode="browse", show="headings")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=24, minwidth=24, stretch=False)
        for c, t, w, st in (("godz", "Godziny", 96, False), ("czas", "Czas", 64, False), ("opis", "Czynność", 300, True)):
            self.tree.heading(c, text=t, anchor="w")
            self.tree.column(c, width=w, minwidth=40, stretch=st, anchor="w")
        self.tree.tag_configure("drop", background="#3d5488")  # pierwszy = najwyzszy priorytet tla
        self.tree.tag_configure("folder", foreground=WARN, font=FONT_B, background=BAND)
        self.tree.tag_configure("loose", foreground=MUTED, font=FONT_B, background=BAND)
        self.tree.tag_configure("child", background=CHILD)
        self.tree.tag_configure("run", foreground=RED)
        self.tree.tag_configure("dragsrc", foreground="#5c6370")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<Configure>", self._fit_columns)
        self.tree.bind("<Double-1>", self._dbl)
        self.tree.bind("<Return>", lambda e: self._edit_sel())
        self.tree.bind("<Delete>", lambda e: self._delete_sel())
        self.tree.bind("<Button-3>", self._ctx)
        self.tree.bind("<ButtonPress-1>", self._dnd_press, add="+")
        self.tree.bind("<B1-Motion>", self._dnd_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._dnd_release, add="+")
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._remember_open(True))
        self.tree.bind("<<TreeviewClose>>", lambda e: self._remember_open(False))

        self.menu = tk.Menu(p, tearoff=0, bg=BG2, fg=FG, activebackground=ACC)
        self.move_menu = tk.Menu(self.menu, tearoff=0, bg=BG2, fg=FG, activebackground=ACC)
        self.fmenu = tk.Menu(p, tearoff=0, bg=BG2, fg=FG, activebackground=ACC)
        self.fmenu.add_command(label="Zmień nazwę", command=self._edit_sel)
        self.fmenu.add_command(label="Usuń katalog", command=self._delete_sel)
        self.fmenu.add_separator()
        self.fmenu.add_command(label="Zwiń wszystkie", command=lambda: self._all_folders(True))
        self.fmenu.add_command(label="Rozwiń wszystkie", command=lambda: self._all_folders(False))
        self._apply_details()

    def _details(self):
        return bool(self.s.settings.get("details"))

    def _apply_details(self):
        on = self._details()
        self.tree["displaycolumns"] = ("godz", "czas", "opis") if on else ("czas", "opis")
        self._fit_columns()
        self.det_btn.configure(text="Szczegóły ▾" if on else "Szczegóły ▸")
        self.hint.configure(text="dwuklik: katalog – zwiń/rozwiń, czynność – edytuj · przeciągnij czynność na katalog")

    def _fit_columns(self, _e=None):
        """Ostatnia kolumna zawsze do prawej krawedzi - tlo wierszy na cala szerokosc."""
        w = self.tree.winfo_width()
        if w < 50:
            return
        other = sum(self.tree.column(c, "width") for c in self.tree["displaycolumns"] if c != "opis")
        self.tree.column("opis", width=max(120, w - other - 2))

    def _toggle_details(self):
        self.s.settings["details"] = not self._details()
        self.s.save()
        self._apply_details()
        self._fill_panel()

    def _shift(self, d):
        self.sel = dt.date.today() if d == 0 else self.sel + dt.timedelta(days=d)
        self._fill_panel()

    @staticmethod
    def _round_label(step):
        return f"↑ {step} min" if step else "bez zaokr."

    def _round_changed(self, step):
        self.s.settings["rounding"] = step
        self.round_mb.configure(text=self._round_label(step))
        self.s.save()
        self.refresh()

    def _shown_sec(self, e):
        sec = Store.dur(e)
        return sec if e.get("run") else round_sec(sec, self.s.settings.get("rounding", 15))

    def _row_values(self, e):
        if e.get("start") is not None:
            godz = f"{hhmm(e['start'])}–{'teraz' if e.get('run') else hhmm(e['end'])}"
        else:
            godz = f"dod. {hhmm(e['created'])}"
        sec = self._shown_sec(e)
        return (godz, fmt_dur(sec) if sec else "–", e["desc"])

    def _fill_panel(self):
        d, today = self.sel, dt.date.today()
        rel = "Dziś, " if d == today else "Wczoraj, " if d == today - dt.timedelta(days=1) else ""
        dni = ["pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]
        self.day_lbl.configure(text=f"{rel}{dni[d.weekday()]} {d.strftime('%d.%m')}")
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        entries = self.s.day(d)
        run = lambda e: ("run",) if e.get("run") else ()
        folders = self.s.day_folders(d)
        ids = {f["id"] for f in folders}
        for f in folders:
            kids = [e for e in entries if e.get("folder") == f["id"]]
            tot = sum(self._shown_sec(e) for e in kids)
            fiid = "f:" + f["id"]
            shut = bool(f.get("collapsed"))
            self.tree.insert("", "end", iid=fiid, tags=("folder",), open=not shut,
                             values=("", fmt_dur(tot) if tot else "",
                                     f"{'▸' if shut else '▾'} 📁 {f['name']}  ({len(kids)})"))
            for i, e in enumerate(kids):
                g, c, o = self._row_values(e)
                line = "└ " if i == len(kids) - 1 else "├ "
                self.tree.insert(fiid, "end", iid=e["id"], tags=("child",) + run(e), values=(g, c, line + o))
        loose = [e for e in entries if e.get("folder") not in ids]
        if folders:  # naglowek oddzielajacy czynnosci bez katalogu (i cel upuszczenia)
            tot = sum(self._shown_sec(e) for e in loose)
            self.tree.insert("", "end", iid="sep:loose", tags=("loose",),
                             values=("", fmt_dur(tot) if tot else "", f"Bez katalogu  ({len(loose)})"))
        for e in loose:
            self.tree.insert("", "end", iid=e["id"], tags=run(e), values=self._row_values(e))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self._fill_summary()

    def _fill_summary(self):
        tot = sum(self._shown_sec(e) for e in self.s.day(self.sel))
        self.tot_lbl.configure(text=f"Razem: {fmt_dur(tot)}" if tot else "")

    def _remember_open(self, state):
        iid = self.tree.focus()
        if iid.startswith("f:"):
            self._open[iid[2:]] = state

    # ---------- drag & drop do katalogow
    def _dnd_press(self, ev):
        iid = self.tree.identify_row(ev.y)
        self._dnd = {"iid": iid, "y": ev.y, "active": False, "target": None, "ghost": None,
                     "folder": iid.startswith("f:")} if iid and not iid.startswith("sep:") else None

    def _toggle_folder(self, fid, state=None):
        f = self.s.folder(fid)
        if f:
            f["collapsed"] = (not f.get("collapsed")) if state is None else state
            self.s.save()
            self._fill_panel()

    def _all_folders(self, collapsed):
        for f in self.s.day_folders(self.sel):
            f["collapsed"] = collapsed
        self.s.save()
        self._fill_panel()

    def _ghost(self, d, text):
        """Pływająca etykieta przy kursorze - widac, co jest przenoszone i dokad."""
        if d["ghost"] is None:
            g = tk.Toplevel(self.panel)
            g.overrideredirect(True)
            g.attributes("-topmost", True)
            try:
                g.attributes("-alpha", 0.92)
            except tk.TclError:
                pass
            lbl = tk.Label(g, bg=ACC, fg="#fff", font=FONT_B, padx=10, pady=4, justify="left")
            lbl.pack()
            d["ghost"] = (g, lbl)
        g, lbl = d["ghost"]
        lbl.configure(text=text)
        x, y = self.panel.winfo_pointerxy()
        g.geometry(f"+{x + 16}+{y + 12}")
        g.lift()

    def _dnd_end_visuals(self, d):
        if d.get("ghost"):
            d["ghost"][0].destroy()
            d["ghost"] = None
        self.tree.configure(cursor="")
        self._dnd_mark(d["target"] or "sep:loose", False)
        if self.tree.exists(d["iid"]):
            tags = [t for t in self.tree.item(d["iid"], "tags") if t != "dragsrc"]
            self.tree.item(d["iid"], tags=tags)
        self._apply_details()

    def _dnd_target(self, ev):
        t = self.tree.identify_row(ev.y)
        if not t or t.startswith("sep:"):
            return ""  # puste miejsce / naglowek "Bez katalogu"
        if t.startswith("f:"):
            return t
        par = self.tree.parent(t)
        return par if par.startswith("f:") else ""

    def _dnd_mark(self, iid, on):
        if iid and self.tree.exists(iid):
            tags = [t for t in self.tree.item(iid, "tags") if t != "drop"]
            self.tree.item(iid, tags=(["drop"] if on else []) + tags)

    def _dnd_motion(self, ev):
        d = self._dnd
        if not d or d["folder"]:
            return
        e = next((x for x in self.s.entries if x["id"] == d["iid"]), None)
        if e is None:
            return
        if not d["active"] and abs(ev.y - d["y"]) > 6:
            d["active"] = True
            d["target"] = None
            self.tree.configure(cursor="fleur")
            self.tree.item(d["iid"], tags=["dragsrc"] + list(self.tree.item(d["iid"], "tags")))
        if d["active"]:
            h = self.tree.winfo_height()  # przewijanie przy krawedziach
            if ev.y < 28:
                self.tree.yview_scroll(-1, "units")
            elif ev.y > h - 10:
                self.tree.yview_scroll(1, "units")
            t = self._dnd_target(ev)
            if t != d["target"]:
                self._dnd_mark(d["target"] or "sep:loose", False)
                self._dnd_mark(t or "sep:loose", True)
                d["target"] = t
            name = self.s.folder(t[2:])["name"] if t else "Bez katalogu"
            desc = e["desc"] if len(e["desc"]) <= 40 else e["desc"][:39] + "…"
            self._ghost(d, f"✋ {desc}\n→ {name}")
            self.hint.configure(text=f"Upuść → {name}")
            return "break"

    def _dnd_release(self, ev):
        d, self._dnd = self._dnd, None
        if not d:
            return
        if d["folder"]:  # katalog zwija sie dwuklikiem (_dbl)
            return
        if not d["active"]:
            return
        self._dnd_end_visuals(d)
        e = next((x for x in self.s.entries if x["id"] == d["iid"]), None)
        t = self._dnd_target(ev)
        if e is not None:
            self._move_to(e, t[2:] if t else None)
        return "break"

    def _move_to(self, e, fid):
        e["folder"] = fid
        f = self.s.folder(fid) if fid else None
        if f:
            f["collapsed"] = False  # pokaz, gdzie wyladowala czynnosc
        self.s.save()
        self.refresh()

    # ---------- katalogi
    def _new_folder(self):
        def ok(name):
            if not name.strip():
                return "Podaj nazwę katalogu"
            f = self.s.add_folder(name, self.sel)
            # zaznaczona czynnosc trafia od razu do nowego katalogu
            kind, e = self._sel_entry()
            if kind == "entry" and e:
                e["folder"] = f["id"]
            self.s.save()
            self.refresh()
        self._folder_dialog("Nowy katalog", "", ok)

    def _folder_dialog(self, title, name, on_ok):
        w = tk.Toplevel(self.panel)
        w.title(title)
        w.attributes("-toolwindow", True)
        w.attributes("-topmost", True)
        w.configure(bg=BG)
        w.transient(self.panel)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Nazwa").grid(row=0, column=0, sticky="w")
        v = tk.StringVar(value=name)
        cb = ttk.Entry(f, textvariable=v, width=36)
        cb.grid(row=0, column=1, sticky="we", pady=3, padx=(6, 0))
        err = ttk.Label(f, text="", foreground=RED)
        err.grid(row=1, column=0, columnspan=2, sticky="w")

        def ok(*_):
            msg = on_ok(v.get())
            if msg:
                err.configure(text=msg)
            else:
                w.destroy()

        bf = ttk.Frame(f)
        bf.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(bf, text="Anuluj", command=w.destroy).pack(side="right")
        ttk.Button(bf, text="Zapisz", command=ok).pack(side="right", padx=6)
        w.bind("<Return>", ok)
        w.bind("<Escape>", lambda e: w.destroy())
        w.update_idletasks()
        w.geometry(f"+{self.panel.winfo_rootx() + 20}+{self.panel.winfo_rooty() + 40}")
        w.grab_set()
        cb.focus_force()
        cb.icursor("end")

    # ---------- panel actions
    def _sel_entry(self):
        sel = self.tree.selection()
        if not sel:
            return None, None
        iid = sel[0]
        if iid.startswith("f:"):
            return "folder", self.s.folder(iid[2:])
        if iid.startswith("sep:"):
            return None, None
        return "entry", next((e for e in self.s.entries if e["id"] == iid), None)

    def _ctx(self, ev):
        iid = self.tree.identify_row(ev.y)
        if not iid or iid.startswith("sep:"):
            return
        self.tree.selection_set(iid)
        if iid.startswith("f:"):
            self.fmenu.tk_popup(ev.x_root, ev.y_root)
            return
        e = next((x for x in self.s.entries if x["id"] == iid), None)
        m, mm = self.menu, self.move_menu
        m.delete(0, "end")
        mm.delete(0, "end")
        m.add_command(label="Edytuj", command=self._edit_sel)
        m.add_command(label="Nagrywaj czas tej czynności", command=self._resume_sel)
        m.add_command(label="Duplikuj na dziś", command=self._dup_sel)
        folders = self.s.day_folders(self.sel)
        if folders and e:
            for f in folders:
                mm.add_command(label=f["name"], command=lambda fid=f["id"]: self._move_to(e, fid))
            mm.add_separator()
            mm.add_command(label="— bez katalogu —", command=lambda: self._move_to(e, None))
            m.add_cascade(label="Przenieś do katalogu", menu=mm)
        m.add_separator()
        m.add_command(label="Usuń", command=self._delete_sel)
        m.tk_popup(ev.x_root, ev.y_root)

    def _dbl(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid and iid.startswith("f:"):  # dwuklik na katalogu = zwin / rozwin
            self._toggle_folder(iid[2:])
            return "break"
        if iid:
            self.tree.selection_set(iid)
            self._edit_sel()
            return "break"

    def _edit_sel(self):
        kind, obj = self._sel_entry()
        if kind == "folder" and obj:
            f = obj

            def ok(name):
                if not name.strip():
                    return "Podaj nazwę katalogu"
                f["name"] = name.strip()
                self.s.save()
                self.refresh()
            self._folder_dialog("Zmień nazwę katalogu", f["name"], ok)
        elif kind == "entry" and obj:
            e = obj
            orig = fmt_dur(Store.dur(e)) if Store.dur(e) else ""
            self._dialog("Edytuj czynność", e["desc"], orig, e.get("run"),
                         lambda desc, m: self._save_edit(e, desc, m, orig))

    def _save_edit(self, e, desc, m_txt, orig):
        if not desc.strip():
            return "Opis nie może być pusty"
        if not e.get("run") and m_txt.strip() != orig:
            if m_txt.strip():
                mins = parse_mins(m_txt)
                if mins is None:
                    return "Czas wpisz np. 45, 1h30 albo 1:30"
                e["mins"] = mins
            else:
                e.update(start=None, end=None, mins=None)
        e["desc"] = desc.strip()
        self.s.save()
        self.refresh()

    def _resume_sel(self):
        kind, e = self._sel_entry()
        if kind == "entry" and e:
            self.s.start_timer(e["desc"])
            self.s.save()
            self.sel = dt.date.today()
            self.refresh()

    def _dup_sel(self):
        kind, e = self._sel_entry()
        if kind == "entry" and e:
            self.s.add_plain(e["desc"], dt.date.today())
            self.s.save()
            self.refresh()
            self._flash("✔ dopisano na dziś")

    def _delete_sel(self):
        kind, obj = self._sel_entry()
        if kind == "folder" and obj:
            if messagebox.askyesno("Usuń katalog", f"Usunąć katalog „{obj['name']}”?\n"
                                                   "Czynności z niego zostaną na liście.", parent=self.panel):
                self.s.remove_folder(obj)
                self.s.save()
                self.refresh()
        elif kind == "entry" and obj and messagebox.askyesno("Usuń", f"Usunąć „{obj['desc']}”?", parent=self.panel):
            self.s.remove(obj)
            self.s.save()
            self.refresh()

    def _dialog(self, title, desc, mins, running, on_ok):
        w = tk.Toplevel(self.panel)
        w.title(title)
        w.attributes("-toolwindow", True)
        w.attributes("-topmost", True)
        w.configure(bg=BG)
        w.transient(self.panel)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        v_desc, v_m = tk.StringVar(value=desc), tk.StringVar(value=mins)
        ttk.Label(f, text="Czynność").grid(row=0, column=0, sticky="w")
        ed = ttk.Entry(f, textvariable=v_desc, width=44)
        ed.grid(row=0, column=1, columnspan=2, sticky="we", pady=3)
        ttk.Label(f, text="Czas").grid(row=1, column=0, sticky="w")
        me = ttk.Entry(f, textvariable=v_m, width=9)
        me.grid(row=1, column=1, sticky="w", pady=3)
        if running:
            me.configure(state="disabled")
        ttk.Label(f, text="trwa – zatrzymaj nagrywanie, aby zmienić" if running else "np. 45, 1h30, 1:30 – albo puste",
                  style="Muted.TLabel").grid(row=1, column=2, sticky="w", padx=6)
        err = ttk.Label(f, text="", foreground=RED)
        err.grid(row=2, column=0, columnspan=3, sticky="w")

        def ok(*_):
            msg = on_ok(v_desc.get(), v_m.get())
            if msg:
                err.configure(text=msg)
            else:
                w.destroy()

        bf = ttk.Frame(f)
        bf.grid(row=3, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(bf, text="Anuluj", command=w.destroy).pack(side="right")
        ttk.Button(bf, text="Zapisz", command=ok).pack(side="right", padx=6)
        w.bind("<Return>", ok)
        w.bind("<Escape>", lambda e: w.destroy())
        w.update_idletasks()
        w.geometry(f"+{self.panel.winfo_rootx() + 20}+{self.panel.winfo_rooty() + 40}")
        w.grab_set()
        (me if desc and not running else ed).focus_force()

    # ---------- export
    def _sections(self, d):
        """[(nazwa katalogu albo None, [wpisy])] - katalogi z wpisami, potem reszta."""
        entries = self.s.day(d)
        folders = self.s.day_folders(d)
        ids = {f["id"] for f in folders}
        out = [(f["name"], [e for e in entries if e.get("folder") == f["id"]]) for f in folders]
        out = [(n, es) for n, es in out if es]
        rest = [e for e in entries if e.get("folder") not in ids]
        if rest:
            out.append((None, rest))
        return out

    def _summary_text(self, d):
        step = self.s.settings.get("rounding", 15)
        secs = self._sections(d)
        if not secs:
            return None
        lines, grand = [ymd(d)], 0
        foldered = any(n for n, _ in secs)
        for name, es in secs:
            gs = groups(es, step)
            tot = sum(o["sec"] for o in gs)
            grand += tot
            if foldered:
                lines.append(f"{name or 'Pozostałe'}" + (f" – {fmt_dur(tot)}" if tot else ""))
            for o in gs:
                lines.append(("  - " if foldered else "") + o["desc"] + (f" – {fmt_dur(o['sec'])}" if o["sec"] else ""))
        if grand:
            lines.append(f"Razem: {fmt_dur(grand)}")
        return "\n".join(lines)

    def _clip(self, txt):
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self.root.update()
        self._flash("✔ skopiowano do schowka")

    def copy_summary(self):
        t = self._summary_text(self.sel)
        self._clip(t) if t else self._flash("brak wpisów")

    def copy_list(self):
        secs = self._sections(self.sel)
        foldered = any(n for n, _ in secs)
        rows = []
        for name, es in secs:
            if foldered:
                rows.append(name or "Pozostałe")
            for e in es:
                sec = self._shown_sec(e)
                rows.append(("  - " if foldered else "") + e["desc"] + (f" – {fmt_dur(sec)}" if sec else ""))
        self._clip("\n".join(rows)) if rows else self._flash("brak wpisów")

    def export(self, scope):
        d = self.sel
        if scope == "day":
            a, b, name = d, d + dt.timedelta(days=1), f"czas-{ymd(d)}.csv"
        elif scope == "week":
            a = d - dt.timedelta(days=d.weekday())
            b, name = a + dt.timedelta(days=7), f"czas-tydzien-{ymd(a)}.csv"
        else:
            a = d.replace(day=1)
            b = (a + dt.timedelta(days=32)).replace(day=1)
            name = f"czas-{a.strftime('%Y-%m')}.csv"
        rows = self.s.between(a, b)
        if not rows:
            return self._flash("brak wpisów w tym okresie")
        path = filedialog.asksaveasfilename(parent=self.panel, initialfile=name, defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        step = self.s.settings.get("rounding", 15)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Data", "Katalog", "Czynność", "Minuty", "Start", "Koniec"])
            for e in rows:
                timed = e.get("start") is not None
                sec = round_sec(Store.dur(e), step)
                fo = self.s.folder(e.get("folder")) if e.get("folder") else None
                w.writerow([e["day"], fo["name"] if fo else "", e["desc"], int(round(sec / 60)) if sec else "",
                            hhmm(e["start"]) if timed else "",
                            ("" if e.get("run") else hhmm(e["end"])) if timed else ""])
        self._flash("✔ zapisano CSV")


def single_instance():
    ctypes.windll.kernel32.CreateMutexW(None, False, "TimeRecML_single_instance_mutex")
    return ctypes.windll.kernel32.GetLastError() != 183


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if not single_instance():
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo(APP, "Aplikacja już działa (Ctrl+Alt+T pokazuje pasek).")
        sys.exit(0)
    App().root.mainloop()
