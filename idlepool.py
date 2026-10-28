#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2026 IdlePool Contributors

IdlePool Desktop App — Tək faylda FastAPI server + tkinter masaüstü interfeysi + Agent.
Asılılıqlar: pip install fastapi "uvicorn[standard]" websockets psutil paramiko  (tkinter Python ilə gəlir; paramiko yalnız daxili canlı SSH terminalı üçün lazımdır)
İstifadə: python idlepool_app.py server (və ya agent)
"""

import argparse, asyncio, json, math, os, platform, secrets, socket, string, subprocess, sys, threading, time, uuid
from contextlib import asynccontextmanager
from pathlib import Path

if os.environ.get("IDLEPOOL_DATA"):
    DATA_FILE = Path(os.environ["IDLEPOOL_DATA"]).expanduser()
elif getattr(sys, "frozen", False):
    DATA_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "IdlePool" / "idlepool_data.json"
else:
    DATA_FILE = Path("idlepool_data.json")
START_KUDOS = 100.0
ONLINE_SEC = 30
EXEC_TIMEOUT = 60      # bir əmrin server tərəfində gözlənilmə həddi (saniyə)
EXEC_MAX_OUT = 20000   # saxlanılan nəticənin maksimum simvol sayı


# ───────────────────────── DESKTOP UI (tkinter) ─────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:  # tkinter yoxdursa yalnız agent rejimi işləyəcək
    tk = ttk = ScrolledText = None
import queue, re, subprocess, tempfile, urllib.request, urllib.error
try:
    from tkinter import messagebox
except ImportError:
    messagebox = None
try:
    import psutil
except ImportError:
    psutil = None
try:
    import paramiko
except ImportError:
    paramiko = None
HIST = 40

F = "Segoe UI"
SIDE, SIDE2, SIDETX = "#0a3248", "#0f4560", "#b9d4de"
PANE, CARD, TX, MU, BD = "#eef3f5", "#ffffff", "#0e2230", "#5a7180", "#d6e2e8"
AC, ACD, GOLD, GOLDD = "#0e7c90", "#0b6677", "#ffd75e", "#9a6f00"
WHY = {"completed": "müddət bitdi", "provider_offline": "cihaz oflayn oldu",
       "closed_by_buyer": "alıcı dayandırdı", "closed_by_provider": "provayder dayandırdı"}
KEY_FILE = Path.home() / ".idlepool_key"


def fmt_left(s):
    s = max(0, int(s)); h, m = s // 3600, s % 3600 // 60
    return f"{h} saat {m} dəq" if h else (f"{m} dəq" if m else f"{s} san")



# ───────── optimallaşdırma köməkçiləri (təhlükəsiz və geri qaytarıla bilən) ─────────
SETTINGS_FILE = Path.home() / ".idlepool_settings.json"
HIGH_PLAN = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
PROTECT = {"system", "system idle process", "registry", "memory compression", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
           "lsass.exe", "winlogon.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe",
           "ctfmon.exe", "searchhost.exe", "startmenuexperiencehost.exe", "securityhealthservice.exe", "msmpeng.exe", "audiodg.exe"}
OPT_STEPS = (("mem", "RAM-ı boşalt"), ("temp", "Müvəqqəti faylları təmizlə"), ("plan", "Enerji planını yüksək performansa keçir"),
             ("prio", "IdlePool prioritetini artır"), ("awake", "Server işləyərkən yuxu rejimini blokla"))


def _powercfg(*a):
    return subprocess.run(["powercfg", *a], capture_output=True, text=True, timeout=10, creationflags=0x08000000)


def active_plan():
    m = re.search(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", _powercfg("/getactivescheme").stdout)
    return m.group(1).lower() if m else None


def trim_memory():
    """Windows: işləyən proqramların istifadə etmədiyi yaddaşı diskə köçürür. Heç bir proqramı bağlamır."""
    import ctypes
    k, ps = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]; k.OpenProcess.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]; ps.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
    n = 0
    for pr in psutil.process_iter(["pid"]):
        h = k.OpenProcess(0x0500, 0, pr.info["pid"])  # QUERY_INFORMATION | SET_QUOTA
        if h:
            try: n += 1 if ps.EmptyWorkingSet(h) else 0
            finally: k.CloseHandle(h)
    return n


def clean_temp(max_age_h=24):
    """Yalnız istifadəçinin müvəqqəti qovluğunda 24 saatdan köhnə faylları silir. Açıq fayllar keçilir."""
    root, now, freed, cnt = tempfile.gettempdir(), time.time(), 0, 0
    for d, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for f in files:
            fp = os.path.join(d, f)
            try:
                if os.path.islink(fp): continue
                st = os.stat(fp)
                if now - st.st_mtime > max_age_h * 3600:
                    os.remove(fp); freed += st.st_size; cnt += 1
            except OSError: pass
        for sd in dirs:
            try: os.rmdir(os.path.join(d, sd))  # yalnız boş qovluqlar silinir
            except OSError: pass
    return cnt, freed


def agent_command():
    exe = f'"{sys.executable}"'
    if not getattr(sys, "frozen", False):
        exe += f' "{Path(sys.argv[0]).resolve()}"'
    return ("& " if os.name == "nt" else "") + exe


class IdlePoolApp(tk.Tk if tk else object):
    def __init__(self, host, port):
        super().__init__()
        self.base = f"http://{host}:{port}"
        self.key = KEY_FILE.read_text().strip() if KEY_FILE.exists() else ""
        self.me, self.nodes, self.orders, self.leases = None, [], [], []
        self.online, self.show_key, self.busy, self.q = False, False, False, queue.Queue()
        self.toast_job, self.info, self.hist = None, None, []
        self.prev_plan, self.opt_busy, self.awake_on = None, False, False
        self.term_lease, self.term_shown, self.term_busy = None, set(), False
        self.pending_select_lease = None
        self.ssh_client, self.ssh_channel, self.ssh_connected = None, None, False
        self.opt = {"auto": True, **{k: True for k, _ in OPT_STEPS}}
        try: self.opt.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception: pass
        self.title("IdlePool"); self.geometry("1180x740"); self.minsize(940, 600); self.configure(bg=PANE)
        self.style()
        self.build()
        self.bind("<F5>", lambda e: self.refresh())
        for i, p in enumerate(("market", "order", "leases", "device", "optimize", "account"), 1):
            self.bind(f"<Control-Key-{i}>", lambda e, p=p: self.go(p))
        if psutil: psutil.cpu_percent(interval=None)
        self.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.go("market"); self.pump(); self.tick(); self.sys_tick(); self.refresh()
        if self.opt["auto"]: self.after(1200, self.run_opt)

    # ── ümumi köməkçilər ──
    def api(self, path, method="GET", body=None, key=None):
        req = urllib.request.Request(self.base + path, method=method,
              data=json.dumps(body).encode() if body is not None else None,
              headers={"Content-Type": "application/json", "X-Key": self.key if key is None else key})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try: d = json.loads(e.read()).get("detail")
            except Exception: d = None
            raise RuntimeError(d if isinstance(d, str) else "Daxil edilən dəyərlər düzgün deyil")

    def pump(self):
        try:
            while True: self.q.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self.pump)

    def tick(self):
        self.refresh(); self.after(5000, self.tick)

    def bg(self, fn, ok=None):
        def work():
            try: r, err = fn(), None
            except Exception as e: r, err = None, e
            self.q.put(lambda: self.done(r, err, ok))
        threading.Thread(target=work, daemon=True).start()

    def done(self, r, err, ok):
        if err: self.say(str(err), True)
        elif ok: ok(r)
        self.refresh()

    def say(self, text, bad=False):
        self.toast.config(text=text, bg="#8a2a18" if bad else SIDE)
        self.toast.place(relx=1, rely=1, x=-20, y=-20, anchor="se"); self.toast.lift()
        if self.toast_job: self.after_cancel(self.toast_job)
        self.toast_job = self.after(3800, self.toast.place_forget)

    def authed(self):
        if self.me: return True
        self.go("account"); self.say("Əvvəl hesaba daxil ol.", True); return False

    def num(self, var, name, integer=False):
        try:
            v = float(var.get().replace(",", "."))
            if v <= 0: raise ValueError
            return int(v) if integer else v
        except ValueError:
            raise RuntimeError(f"“{name}” sıfırdan böyük rəqəm olmalıdır")

    def set_key(self, k):
        self.key = k
        try:
            if k: KEY_FILE.write_text(k)
            else: KEY_FILE.unlink(missing_ok=True)
        except OSError: pass

    # ── görünüş ──
    def style(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TX, rowheight=36,
                    borderwidth=0, font=(F, 10))
        s.configure("Treeview.Heading", background=PANE, foreground=MU, font=(F, 9, "bold"), relief="flat", padding=(8, 6))
        s.map("Treeview", background=[("selected", AC)], foreground=[("selected", "white")])
        s.map("Treeview.Heading", background=[("active", PANE)])
        s.configure("A.TButton", background=AC, foreground="white", padding=(16, 7), borderwidth=0, font=(F, 10, "bold"))
        s.map("A.TButton", background=[("disabled", "#a9c3ca"), ("active", ACD)], foreground=[("disabled", "white")])
        s.configure("G.TButton", background=CARD, foreground=TX, padding=(14, 6), bordercolor=BD, font=(F, 10))
        s.map("G.TButton", background=[("active", "#dff1f4")])
        s.configure("D.TButton", background=CARD, foreground="#b3361f", padding=(14, 6), bordercolor=BD, font=(F, 10))
        s.configure("TEntry", padding=6, fieldbackground="white", bordercolor=BD)
        s.configure("TCombobox", padding=5, fieldbackground="white", bordercolor=BD)
        s.configure("TCheckbutton", background=PANE, foreground=MU, font=(F, 10))
        s.configure("C.TCheckbutton", background=CARD, foreground=TX, font=(F, 10))
        s.configure("Horizontal.TProgressbar", troughcolor=BD, background=AC, borderwidth=0, thickness=8)
        s.configure("TSpinbox", padding=5, fieldbackground="white", bordercolor=BD)

    def lbl(self, p, t, size=10, bold=False, fg=TX, bg=None, **kw):
        return tk.Label(p, text=t, font=(F, size, "bold" if bold else "normal"), fg=fg,
                        bg=bg or p.cget("bg"), **kw)

    def head(self, f, title, sub):
        h = tk.Frame(f, bg=PANE); h.pack(fill="x", padx=28, pady=(24, 14))
        self.lbl(h, title, 21, True).pack(anchor="w"); self.lbl(h, sub, 10, fg=MU).pack(anchor="w")

    def card(self, p):
        return tk.Frame(p, bg=CARD, highlightbackground=BD, highlightthickness=1)

    def tree(self, p, cols, height=12):
        box = tk.Frame(p, bg=CARD, highlightbackground=BD, highlightthickness=1)
        t = ttk.Treeview(box, columns=[c[0] for c in cols], show="headings", selectmode="browse", height=height)
        for k, name, w, anc in cols:
            t.heading(k, text=name); t.column(k, width=w, anchor=anc, stretch=(k == cols[0][0]))
        sb = ttk.Scrollbar(box, command=t.yview); t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); t.pack(fill="both", expand=True)
        t.tag_configure("off", foreground=MU)
        return box, t

    def empty(self, box, title, text):
        l = tk.Label(box, text=f"{title}\n{text}", bg=CARD, fg=MU, font=(F, 10), justify="center")
        l.configure(); return l

    def build(self):
        self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(0, weight=1)
        side = tk.Frame(self, bg=SIDE, width=228); side.grid(row=0, column=0, sticky="ns"); side.pack_propagate(False)
        b = tk.Frame(side, bg=SIDE); b.pack(fill="x", padx=18, pady=(20, 18))
        self.lbl(b, "IdlePool", 18, True, "white").pack(anchor="w")
        self.lbl(b, "Boş cihaz bazarı", 9, fg=SIDETX).pack(anchor="w")
        self.navs = {}
        for key, text in (("market", "Bazar"), ("order", "Sifariş ver"), ("leases", "İcarələrim"), ("device", "Cihazımı sat"), ("optimize", "Optimallaşdır")):
            row = tk.Frame(side, bg=SIDE); row.pack(fill="x", padx=8, pady=1)
            bar = tk.Frame(row, bg=SIDE, width=3); bar.pack(side="left", fill="y")
            l = tk.Label(row, text=text, anchor="w", bg=SIDE, fg=SIDETX, font=(F, 11), padx=14, pady=9, cursor="hand2")
            l.pack(side="left", fill="x", expand=True)
            for w in (row, l): w.bind("<Button-1>", lambda e, k=key: self.go(k))
            l.bind("<Enter>", lambda e, l=l, k=key: l.config(bg=SIDE2) if self.page != k else None)
            l.bind("<Leave>", lambda e, l=l, k=key: l.config(bg=SIDE2 if self.page == k else SIDE))
            self.navs[key] = (row, bar, l, text)
        tk.Frame(side, bg=SIDE).pack(fill="both", expand=True)
        me = tk.Frame(side, bg=SIDE2, cursor="hand2"); me.pack(fill="x", padx=12, pady=12)
        self.me_name = self.lbl(me, "Daxil olmamısan", 9, fg=SIDETX, bg=SIDE2); self.me_name.pack(anchor="w", padx=12, pady=(10, 0))
        self.me_k = self.lbl(me, "—", 15, True, GOLD, SIDE2); self.me_k.pack(anchor="w", padx=12, pady=(0, 10))
        for w in (me, self.me_name, self.me_k): w.bind("<Button-1>", lambda e: self.go("account"))

        self.content = tk.Frame(self, bg=PANE); self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1); self.content.grid_columnconfigure(0, weight=1)
        self.pages = {k: tk.Frame(self.content, bg=PANE) for k in ("market", "order", "leases", "device", "optimize", "account")}
        for f in self.pages.values(): f.grid(row=0, column=0, sticky="nsew")
        self.build_market(); self.build_order(); self.build_leases(); self.build_device(); self.build_optimize(); self.build_account()

        st = tk.Frame(self, bg=SIDE, height=28); st.grid(row=1, column=0, columnspan=2, sticky="ew"); st.grid_propagate(False)
        self.s_state = self.lbl(st, "● Qoşulur…", 9, fg=SIDETX); self.s_state.place(x=14, y=4)
        self.s_cnt = self.lbl(st, "", 9, fg=SIDETX); self.s_cnt.place(x=230, y=4)
        self.s_upd = self.lbl(st, "F5 — yenilə", 9, fg=SIDETX); self.s_upd.place(relx=1, x=-14, y=4, anchor="ne")
        self.toast = tk.Label(self.content, bg=SIDE, fg="white", font=(F, 10), padx=16, pady=10, wraplength=380, justify="left")
        self.page = "market"

    def go(self, p):
        self.page = p; self.pages[p].tkraise()
        if p == "optimize" and hasattr(self, "ptree"): self.draw_procs()
        for k, (row, bar, l, _) in self.navs.items():
            on = k == p
            bar.config(bg=GOLD if on else SIDE); l.config(bg=SIDE2 if on else SIDE, fg="white" if on else SIDETX,
                                                          font=(F, 11, "bold" if on else "normal"))

    # ── Bazar ──
    def build_market(self):
        f = self.pages["market"]; self.head(f, "Bazar", "Başqalarının boş cihazlarını saatlıq icarəyə götür.")
        bar = tk.Frame(f, bg=PANE); bar.pack(fill="x", padx=28, pady=(0, 10))
        self.search = tk.StringVar(); self.search.trace_add("write", lambda *a: self.draw_market())
        self.lbl(bar, "Axtar", 10, fg=MU).pack(side="left"); ttk.Entry(bar, textvariable=self.search, width=24).pack(side="left", padx=(8, 14))
        self.sort = ttk.Combobox(bar, values=["Ən ucuz", "Ən çox RAM", "Ən etibarlı"], state="readonly", width=14)
        self.sort.current(0); self.sort.pack(side="left"); self.sort.bind("<<ComboboxSelected>>", lambda e: self.draw_market())
        self.free = tk.BooleanVar(); ttk.Checkbutton(bar, text="Yalnız boş olanlar", variable=self.free, command=self.draw_market).pack(side="left", padx=14)
        self.rent_btn = ttk.Button(bar, text="İcarəyə götür", style="A.TButton", state="disabled", command=self.rent_selected)
        self.rent_btn.pack(side="right"); self.cnt = self.lbl(bar, "", 10, fg=MU); self.cnt.pack(side="right", padx=14)
        bar2 = tk.Frame(f, bg=PANE); bar2.pack(fill="x", padx=28, pady=(0, 10))
        self.lbl(bar2, "Tək kompüterdə sınamaq üçün ikinci (saxta) satıcı yarada bilərsən.", 9, fg=MU).pack(side="left")
        ttk.Button(bar2, text="Sınaq satıcısı yarat", style="G.TButton", command=self.make_test_seller).pack(side="right")
        cols = [("host", "Cihaz", 230, "w"), ("ram", "RAM (GB)", 90, "center"), ("disk", "Disk (GB)", 90, "center"),
                ("cores", "Nüvə", 70, "center"), ("price", "Qiymət (kudos/saat)", 150, "center"),
                ("up", "Etibar", 90, "center"), ("st", "Vəziyyət", 100, "center")]
        box, self.mtree = self.tree(f, cols); box.pack(fill="both", expand=True, padx=28, pady=(0, 24))
        self.mempty = self.empty(box, "Bazarda hələ elan yoxdur", "Öz cihazını əlavə et, elanın burada görünəcək.")
        self.mtree.bind("<<TreeviewSelect>>", lambda e: self.sel_changed()); self.mtree.bind("<Double-1>", lambda e: self.rent_selected())

    def sel_changed(self):
        s = self.mtree.selection(); n = next((x for x in self.nodes if s and x["node_id"] == s[0]), None)
        self.rent_btn.config(state="normal" if n and not n["busy"] else "disabled")

    def make_test_seller(self):
        """Tək kompüterdə sınaq üçün: yeni hesab yaradır və onun adından ayrı pəncərədə agent başladır.
        Bazar öz cihazını göstərmir, ona görə bu, ikinci (saxta) satıcı yaratmağın ən sürətli yoludur."""
        self.say("Sınaq satıcısı yaradılır…")
        def work():
            try: return self.api("/api/register", "POST", {"name": "Test Satıcı"}, key=""), None
            except Exception as e: return None, e
        def done():
            j, err = work()
            self.q.put(lambda: self.finish_test_seller(j, err))
        threading.Thread(target=done, daemon=True).start()

    def finish_test_seller(self, j, err):
        if err: return self.say(str(err), True)
        cmd = f"{agent_command()} agent --key {j['key']} --ram-gb 2 --disk-gb 10 --cores 1 --price 3"
        try:
            if os.name == "nt":
                subprocess.Popen(f'start "IdlePool sınaq satıcı" cmd /k {cmd}', shell=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"'])
            else:
                for term in (["x-terminal-emulator", "-e", cmd], ["gnome-terminal", "--", "bash", "-c", cmd + "; exec bash"], ["xterm", "-e", cmd]):
                    try:
                        subprocess.Popen(term); break
                    except FileNotFoundError:
                        continue
                else:
                    raise FileNotFoundError
            self.say(f"Sınaq satıcısı başladı ({j['name']}) — bir neçə saniyəyə bazarda görünəcək")
        except Exception:
            self.clip(cmd, "Terminal tapılmadı, əmr kopyalandı: " + cmd)

    def draw_market(self):
        sel = self.mtree.selection(); a = list(self.nodes); q = self.search.get().strip().lower()
        if q: a = [x for x in a if q in x["hostname"].lower()]
        if self.free.get(): a = [x for x in a if not x["busy"]]
        i = self.sort.current(); a.sort(key=(lambda x: x["price"], lambda x: -x["ram_gb"], lambda x: -x["uptime"])[max(i, 0)])
        self.mtree.delete(*self.mtree.get_children())
        for n in a:
            self.mtree.insert("", "end", iid=n["node_id"], tags=("off",) if n["busy"] else (),
                values=(n["hostname"], n["ram_gb"], n["disk_gb"], n["cores"], n["price"], f"{round(n['uptime'] * 100)}%", "Məşğul" if n["busy"] else "Boşdur"))
        if sel and self.mtree.exists(sel[0]): self.mtree.selection_set(sel[0])
        self.cnt.config(text=f"{len(a)} elan"); self.sel_changed()
        if a: self.mempty.place_forget()
        else:
            self.mempty.config(text="Heç nə tapılmadı\nAxtarışı və ya süzgəci dəyiş." if self.nodes else
                "Bazarda hələ elan yoxdur\n“Cihazımı sat” bölməsindən öz cihazını əlavə et.")
            self.mempty.place(relx=.5, rely=.4, anchor="center")

    def rent_selected(self):
        s = self.mtree.selection(); n = next((x for x in self.nodes if s and x["node_id"] == s[0]), None)
        if n and not n["busy"]: self.open_rent(n)

    def open_rent(self, n):
        if not self.authed(): return
        d = tk.Toplevel(self); d.title("İcarəyə götür"); d.configure(bg=CARD); d.transient(self); d.resizable(False, False)
        p = tk.Frame(d, bg=CARD); p.pack(padx=26, pady=22)
        self.lbl(p, n["hostname"], 15, True).pack(anchor="w")
        self.lbl(p, f"{n['ram_gb']} GB RAM · {n['disk_gb']} GB disk · {n['cores']} nüvə", 10, fg=MU).pack(anchor="w", pady=(2, 14))
        r = tk.Frame(p, bg=CARD); r.pack(fill="x"); self.lbl(r, "Neçə saat?", 10).pack(side="left")
        hv = tk.StringVar(value="1"); ttk.Spinbox(r, from_=0.5, to=720, increment=0.5, textvariable=hv, width=8).pack(side="right")
        sm = tk.Frame(p, bg=PANE); sm.pack(fill="x", pady=14)
        lines = [self.lbl(sm, "", 10, bg=PANE, anchor="w") for _ in range(3)]
        for l in lines: l.pack(fill="x", padx=12, pady=2)
        lines[0].master.pack_configure(pady=14); lines[0].pack_configure(pady=(10, 2)); lines[2].pack_configure(pady=(2, 10))
        lines[2].config(font=(F, 12, "bold"), fg=GOLDD)
        bt = tk.Frame(p, bg=CARD); bt.pack(fill="x")
        yes = ttk.Button(bt, text="İcarəyə götür", style="A.TButton"); no = ttk.Button(bt, text="Ləğv et", style="G.TButton", command=d.destroy)
        yes.pack(side="right"); no.pack(side="right", padx=8)

        def hours():
            try: return max(0.0, float(hv.get().replace(",", ".")))
            except ValueError: return 0.0
        def calc(*_):
            h = hours(); t = n["price"] * h
            lines[0].config(text=f"Qiymət: {n['price']} kudos/saat"); lines[1].config(text=f"Balansın: {self.me['kudos']:.2f} kudos")
            lines[2].config(text=f"Cəmi: {t:.2f} kudos"); yes.config(state="normal" if 0 < h and t <= self.me["kudos"] else "disabled")
        def confirm():
            h = hours(); d.destroy()
            self.bg(lambda: self.rent(n, h), lambda l: self.after_rent(l, n["hostname"]))
        yes.config(command=confirm); hv.trace_add("write", calc); calc()
        d.update_idletasks(); d.geometry(f"+{self.winfo_x() + (self.winfo_width() - d.winfo_width()) // 2}+{self.winfo_y() + 160}")
        try: d.grab_set()
        except tk.TclError: pass

    def rent(self, n, h):
        j = self.api("/api/orders", "POST", {"ram_gb": n["ram_gb"], "disk_gb": n["disk_gb"], "cores": n["cores"], "hours": h, "max_price": n["price"]})
        b = next((x for x in j["bids"] if x["node_id"] == n["node_id"]), None)
        if not b: raise RuntimeError("Bu elan üçün təklif alınmadı: öz cihazındır və ya məşğuldur.")
        return self.api(f"/api/orders/{j['order']['id']}/accept", "POST", {"bid_id": b["id"]})

    def after_rent(self, lease, hostname):
        self.say("İcarə başladı: " + hostname); self.pending_select_lease = lease.get("id"); self.go("leases")

    # ── Sifariş ──
    def build_order(self):
        f = self.pages["order"]; self.head(f, "Sifariş ver", "Kompüterinin vəziyyətinə bax, lazım olan gücü yaz, təklifləri gör.")
        self.build_machine(f, False)
        c = self.card(f); c.pack(fill="x", padx=28); g = tk.Frame(c, bg=CARD); g.pack(fill="x", padx=18, pady=16)
        self.ov = {}
        for i, (k, name, v) in enumerate((("ram", "RAM (GB)", "1"), ("disk", "Disk (GB)", "1"), ("cores", "Nüvə", "1"),
                                          ("hours", "Müddət (saat)", "1"), ("price", "Maks. qiymət (kudos/saat)", "10"))):
            cell = tk.Frame(g, bg=CARD); cell.grid(row=0, column=i, padx=(0, 14), sticky="w")
            self.lbl(cell, name, 9, fg=MU).pack(anchor="w"); self.ov[k] = tk.StringVar(value=v); ttk.Entry(cell, textvariable=self.ov[k], width=13).pack()
        ttk.Button(c, text="Təklifləri tap", style="A.TButton", command=self.find_bids).pack(anchor="w", padx=18, pady=(0, 16))
        top = tk.Frame(f, bg=PANE); top.pack(fill="x", padx=28, pady=(14, 6)); self.lbl(top, "Təkliflər", 13, True).pack(side="left")
        self.acc_btn = ttk.Button(top, text="Qəbul et", style="A.TButton", state="disabled", command=self.accept_bid); self.acc_btn.pack(side="right")
        box, self.otree = self.tree(f, [("node", "Cihaz", 240, "w"), ("hours", "Müddət", 110, "center"), ("price", "Qiymət (kudos/saat)", 160, "center"), ("total", "Cəmi (kudos)", 130, "center")], 4)
        box.pack(fill="both", expand=True, padx=28, pady=(0, 24)); self.oempty = self.empty(box, "", "")
        self.otree.bind("<<TreeviewSelect>>", lambda e: self.acc_btn.config(state="normal" if self.otree.selection() else "disabled"))
        self.otree.bind("<Double-1>", lambda e: self.accept_bid())

    def find_bids(self):
        if not self.authed(): return
        try:
            body = {"ram_gb": self.num(self.ov["ram"], "RAM"), "disk_gb": self.num(self.ov["disk"], "Disk"), "cores": self.num(self.ov["cores"], "Nüvə", True),
                    "hours": self.num(self.ov["hours"], "Müddət"), "max_price": self.num(self.ov["price"], "Maks. qiymət")}
        except RuntimeError as e: return self.say(str(e), True)
        def ok(j):
            n = len(j["bids"]); self.say(f"{n} təklif tapıldı" if n else "Uyğun təklif yoxdur. Qiyməti artır və ya tələbi azalt.", not n)
        self.bg(lambda: self.api("/api/orders", "POST", body), ok)

    def accept_bid(self):
        s = self.otree.selection()
        if s:
            o, b = s[0].split("|"); self.bg(lambda: self.api(f"/api/orders/{o}/accept", "POST", {"bid_id": b}),
                                            lambda l: self.after_rent(l, l.get("node_id", "")))

    def draw_orders(self):
        sel = self.otree.selection(); self.otree.delete(*self.otree.get_children()); any_ = False
        for o in self.orders:
            if o["state"] != "open": continue
            for b in o["bids"]:
                if b["state"] == "open":
                    any_ = True; self.otree.insert("", "end", iid=f"{o['id']}|{b['id']}", values=(b["node_id"], f"{o['hours']} saat", b["price"], f"{b['price'] * o['hours']:.2f}"))
        if sel and self.otree.exists(sel[0]): self.otree.selection_set(sel[0])
        self.acc_btn.config(state="normal" if self.otree.selection() else "disabled")
        if any_: self.oempty.place_forget()
        else: self.oempty.config(text="Hələ təklif yoxdur\nYuxarıdakı formu doldur və “Təklifləri tap” düyməsinə bas."); self.oempty.place(relx=.5, rely=.45, anchor="center")


    # ── kompüterin vəziyyəti və tövsiyə ──
    def build_machine(self, f, sell=False):
        c = self.card(f); c.pack(fill="x", padx=28, pady=(0, 12)); row = tk.Frame(c, bg=CARD); row.pack(fill="x", padx=14, pady=12)
        m = {"gc": {}}
        for k in ("cpu", "ram", "disk"):
            cv = tk.Canvas(row, width=104, height=104, bg=CARD, highlightthickness=0); cv.pack(side="left", padx=(0, 8)); m["gc"][k] = cv
        m["spk"] = tk.Canvas(row, width=220, height=104, bg=CARD, highlightbackground=BD, highlightthickness=1); m["spk"].pack(side="left", padx=(8, 18))
        r = tk.Frame(row, bg=CARD); r.pack(side="left", fill="both", expand=True)
        self.lbl(r, "Bu kompüter indi", 11, True).pack(anchor="w")
        m["spec"] = self.lbl(r, "", 10, fg=MU, anchor="w", justify="left"); m["spec"].pack(anchor="w", pady=(2, 6))
        bt = tk.Frame(c, bg="#e6f5f8"); bt.pack(fill="x", padx=14, pady=(0, 14))
        m["reco"] = self.lbl(bt, "", 10, bg="#e6f5f8", anchor="w", justify="left", wraplength=520); m["reco"].pack(side="left", padx=12, pady=10)
        ttk.Button(bt, text="Tövsiyə olunanı tətbiq et", style="A.TButton", command=lambda: self.apply_reco(sell)).pack(side="right", padx=12, pady=8)
        self.mach = getattr(self, "mach", {}); self.mach[sell] = m

    def sysinfo(self):
        if not psutil: return None
        vm = psutil.virtual_memory(); du = psutil.disk_usage(Path.home().anchor or "/")
        return {"cpu": psutil.cpu_percent(interval=None), "cores": psutil.cpu_count(logical=True) or 1, "phys": psutil.cpu_count(logical=False) or 1,
                "ram_total": vm.total / 1e9, "ram_free": vm.available / 1e9, "ram_pct": vm.percent,
                "disk_total": du.total / 1e9, "disk_free": du.free / 1e9, "disk_pct": du.percent}

    def recommend(self, sell):
        i = self.info
        if not i: return None
        prices = sorted(n["price"] for n in self.nodes); med = prices[len(prices) // 2] if prices else 5
        ram = i["ram_total"] * 0.5 if not sell else min(i["ram_total"] * 0.5, i["ram_free"] * 0.8)
        return {"ram": max(0.5, round(ram * 2) / 2), "disk": max(1, int(i["disk_free"] * 0.5)), "cores": max(1, i["cores"] // 2),
                "price": max(0.1, round(med if sell else med * 1.2, 1)), "market": bool(prices)}

    def apply_reco(self, sell):
        r = self.recommend(sell)
        if not r: return self.say("Ölçmək üçün psutil lazımdır: pip install psutil", True)
        vs = self.dv if sell else self.ov
        for k in ("ram", "disk", "cores", "price"): vs[k].set(str(r[k]))
        self.say("Tövsiyə olunan ayar yazıldı")

    def gauge(self, cv, pct, label):
        cv.delete("all"); a, b = 10, 94; col = AC if pct < 60 else "#d9a400" if pct < 85 else "#c0392b"
        cv.create_oval(a, a, b, b, outline=BD, width=10)
        cv.create_arc(a, a, b, b, start=90, extent=-max(1, min(359.9, pct * 3.6)), style="arc", outline=col, width=10)
        cv.create_text(52, 46, text=f"{pct:.0f}%", font=(F, 15, "bold"), fill=TX); cv.create_text(52, 66, text=label, font=(F, 9), fill=MU)

    def spark(self, c):
        c.delete("all"); w, h = 220, 104
        for y in (.25, .5, .75): c.create_line(0, h * y, w, h * y, fill=BD)
        n = len(self.hist); step = w / (HIST - 1)
        if n > 1:
            pts = []
            for i, v in enumerate(self.hist): pts += [w - 3 - (n - 1 - i) * step, h - 4 - (h - 24) * v / 100]
            c.create_line(*pts, fill=AC, width=2, smooth=True)
        c.create_text(8, 8, text="CPU yükü (son 80 san)", anchor="nw", font=(F, 8), fill=MU)

    def sys_tick(self):
        i = self.info = self.sysinfo()
        if i:
            self.hist = (self.hist + [i["cpu"]])[-HIST:]
            spec = (f"Prosessor: {i['phys']} nüvə, {i['cores']} axın\nRAM: {i['ram_total']:.1f} GB (boş {i['ram_free']:.1f} GB)\n"
                    f"Disk: {i['disk_total']:.0f} GB (boş {i['disk_free']:.0f} GB)")
            for sell, m in self.mach.items():
                self.gauge(m["gc"]["cpu"], i["cpu"], "CPU"); self.gauge(m["gc"]["ram"], i["ram_pct"], "RAM"); self.gauge(m["gc"]["disk"], i["disk_pct"], "Disk")
                self.spark(m["spk"]); m["spec"].config(text=spec); r = self.recommend(sell)
                line = f"Tövsiyə: RAM {r['ram']} GB · Disk {r['disk']} GB · {r['cores']} nüvə · "
                if sell:
                    why = "RAM-ın və nüvələrin yarısı, boş diskin yarısı təklif edilir ki, kompüter satış zamanı işlək qalsın."
                    line += f"{r['price']} kudos/saat" + ("" if r["market"] else " (bazarda hələ qiymət yoxdur)")
                else:
                    why = ("Kompüterinin gücünün təxminən yarısı kifayətdir; qiymət bazarın orta qiymətindən 20% yuxarıdır ki, daha çox təklif gəlsin."
                           if r["market"] else "Bazarda hələ qiymət yoxdur, standart qiymət göstərilir.")
                    line += f"maks. {r['price']} kudos/saat"
                m["reco"].config(text=line + "\n" + why)
        else:
            for m in self.mach.values(): m["spec"].config(text="Ölçmə üçün psutil lazımdır:\npip install psutil")
        self.after(2000, self.sys_tick)

    # ── Optimallaşdırma ──
    def build_optimize(self):
        f = self.pages["optimize"]; self.head(f, "Optimallaşdır", "Kompüteri server işi üçün hazırla: RAM, müvəqqəti fayllar, enerji planı və yuxu rejimi.")
        c = self.card(f); c.pack(fill="x", padx=28, pady=(0, 12))
        top = tk.Frame(c, bg=CARD); top.pack(fill="x", padx=18, pady=(14, 6))
        self.o_status = self.lbl(top, "Hazırdır", 14, True); self.o_status.pack(side="left")
        self.o_btn = ttk.Button(top, text="İndi optimallaşdır", style="A.TButton", command=self.run_opt); self.o_btn.pack(side="right")
        self.o_prog = ttk.Progressbar(c, maximum=len(OPT_STEPS), style="Horizontal.TProgressbar"); self.o_prog.pack(fill="x", padx=18)
        self.o_sum = self.lbl(c, "", 10, fg=MU, anchor="w", justify="left"); self.o_sum.pack(anchor="w", padx=18, pady=(8, 2))
        self.o_vars, self.o_state, self.o_det = {}, {}, {}
        g = tk.Frame(c, bg=CARD); g.pack(fill="x", padx=18, pady=(4, 6)); g.grid_columnconfigure(2, weight=1)
        for r, (k, name) in enumerate(OPT_STEPS):
            v = tk.BooleanVar(value=self.opt[k]); self.o_vars[k] = v; v.trace_add("write", lambda *a: self.save_opt())
            ttk.Checkbutton(g, text=name, variable=v, style="C.TCheckbutton").grid(row=r, column=0, sticky="w", pady=2)
            self.o_state[k] = self.lbl(g, "", 10, fg=MU, width=3); self.o_state[k].grid(row=r, column=1, padx=8)
            self.o_det[k] = self.lbl(g, "", 10, fg=MU, anchor="w"); self.o_det[k].grid(row=r, column=2, sticky="w")
        self.o_auto = tk.BooleanVar(value=self.opt["auto"]); self.o_auto.trace_add("write", lambda *a: self.save_opt())
        ttk.Checkbutton(c, text="Proqram açılanda avtomatik optimallaşdır", variable=self.o_auto, style="C.TCheckbutton").pack(anchor="w", padx=18, pady=(2, 4))
        self.lbl(c, "Enerji planı proqram bağlananda əvvəlki vəziyyətə qaytarılır. Heç bir proqram öz-özünə bağlanmır.", 9, fg=MU).pack(anchor="w", padx=18, pady=(0, 12))
        h = tk.Frame(f, bg=PANE); h.pack(fill="x", padx=28, pady=(2, 6)); self.lbl(h, "Ən çox RAM işlədən proqramlar", 12, True).pack(side="left")
        ttk.Button(h, text="Seçiləni bağla", style="D.TButton", command=self.close_proc).pack(side="right")
        ttk.Button(h, text="Yenilə", style="G.TButton", command=self.draw_procs).pack(side="right", padx=8)
        box, self.ptree = self.tree(f, [("name", "Proqram", 300, "w"), ("n", "Proses sayı", 110, "center"), ("ram", "RAM (MB)", 120, "center")], 5)
        box.pack(fill="both", expand=True, padx=28, pady=(0, 20))

    def save_opt(self):
        self.opt = {"auto": self.o_auto.get(), **{k: v.get() for k, v in self.o_vars.items()}}
        try: SETTINGS_FILE.write_text(json.dumps(self.opt))
        except OSError: pass

    def draw_procs(self):
        self.ptree.delete(*self.ptree.get_children())
        if not psutil: return
        groups = {}
        for pr in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                nm = (pr.info["name"] or "").strip()
                if not nm or nm.lower() in PROTECT or pr.info["pid"] == os.getpid() or not pr.info["memory_info"]: continue
                g = groups.setdefault(nm, [0, 0]); g[0] += 1; g[1] += pr.info["memory_info"].rss
            except (psutil.Error, OSError): pass
        for nm, (n, rss) in sorted(groups.items(), key=lambda x: -x[1][1])[:12]:
            self.ptree.insert("", "end", iid=nm, values=(nm, n, f"{rss / 1e6:.0f}"))

    def close_proc(self):
        s = self.ptree.selection()
        if not s: return self.say("Əvvəlcə siyahıdan proqram seç.", True)
        nm = s[0]
        if not messagebox.askyesno("Proqramı bağla", f"“{nm}” və onun bütün prosesləri bağlansın?\nYadda saxlanmamış işlər itə bilər.", parent=self): return
        procs = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "") == nm and p.pid != os.getpid()]
        for p in procs:
            try: p.terminate()
            except psutil.Error: pass
        _, alive = psutil.wait_procs(procs, timeout=3)
        for p in alive:
            try: p.kill()
            except psutil.Error: pass
        self.say(f"“{nm}” bağlandı"); self.draw_procs()

    def step_ui(self, k, st, detail):
        self.o_state[k].config(text=st, fg={"✓": "#17814f", "✗": "#b3361f"}.get(st, MU)); self.o_det[k].config(text=detail)

    def run_opt(self):
        if self.opt_busy: return
        if not psutil: return self.say("Optimallaşdırma üçün psutil lazımdır: pip install psutil", True)
        self.opt_busy = True; self.o_btn.config(state="disabled"); self.o_status.config(text="Optimallaşdırılır…"); self.o_prog["value"] = 0
        en = {k: v.get() for k, v in self.o_vars.items()}; before = psutil.virtual_memory().available / 1e9
        for k, _ in OPT_STEPS: self.step_ui(k, "…" if en[k] else "–", "" if en[k] else "söndürülüb")
        def post(k, st, d, n): self.q.put(lambda: (self.step_ui(k, st, d), self.o_prog.config(value=n)))
        def work():
            n = 0; freed = 0
            for k, _ in OPT_STEPS:
                if k == "awake": continue
                n += 1
                if not en[k]: post(k, "–", "söndürülüb", n); continue
                try:
                    if k == "mem":
                        if os.name != "nt": post(k, "–", "yalnız Windows-da", n)
                        else: c = trim_memory(); post(k, "✓", f"{c} prosesin istifadə olunmayan yaddaşı boşaldıldı", n)
                    elif k == "temp":
                        c, freed = clean_temp(); post(k, "✓", f"{c} köhnə fayl silindi, {freed / 1e6:.1f} MB boşaldı", n)
                    elif k == "plan":
                        if os.name != "nt": post(k, "–", "yalnız Windows-da", n)
                        else:
                            cur = active_plan()
                            if cur == HIGH_PLAN: post(k, "✓", "artıq yüksək performansdadır", n)
                            else:
                                ok = _powercfg("/setactive", HIGH_PLAN).returncode == 0 or _powercfg("/setactive", "SCHEME_MIN").returncode == 0
                                if ok:
                                    self.prev_plan = self.prev_plan or cur; post(k, "✓", "yüksək performans planı aktivdir (çıxanda geri qayıdır)", n)
                                else: post(k, "–", "bu kompüterdə yüksək performans planı yoxdur", n)
                    elif k == "prio":
                        pr = psutil.Process()
                        if os.name == "nt": pr.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
                        else: pr.nice(max(-5, pr.nice() - 5))
                        post(k, "✓", "prioritet yüksəldildi", n)
                except Exception as e:
                    post(k, "✗", str(e)[:70] or "alınmadı", n)
            time.sleep(0.6)
            self.q.put(lambda: self.finish_opt(en, before, freed))
        threading.Thread(target=work, daemon=True).start()

    def finish_opt(self, en, before, freed):
        after = psutil.virtual_memory().available / 1e9
        if en["awake"]:
            if os.name == "nt":
                import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000001); self.awake_on = True
                self.step_ui("awake", "✓", "kompüter yuxuya getməyəcək (proqram bağlanana qədər)")
            else: self.step_ui("awake", "–", "yalnız Windows-da")
        elif self.awake_on and os.name == "nt":
            import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000); self.awake_on = False
        self.o_prog["value"] = len(OPT_STEPS); self.opt_busy = False; self.o_btn.config(state="normal")
        self.o_status.config(text="Optimallaşdırma tamamlandı"); self.o_sum.config(text=f"Boş RAM: {before:.1f} → {after:.1f} GB   ·   Son işə salma: {time.strftime('%H:%M:%S')}")
        self.draw_procs(); self.say("Kompüter optimallaşdırıldı")

    def shutdown(self):
        try:
            if self.ssh_connected: self.live_disconnect()
        except Exception: pass
        try:
            if self.awake_on and os.name == "nt":
                import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            if self.prev_plan and os.name == "nt": _powercfg("/setactive", self.prev_plan)
        except Exception: pass
        self.destroy()

    # ── İcarələr ──
    def build_leases(self):
        f = self.pages["leases"]; self.head(f, "İcarələrim", "Aldığın və verdiyin icarələr.")
        top = tk.Frame(f, bg=PANE); top.pack(fill="x", padx=28, pady=(0, 8))
        self.stop_btn = ttk.Button(top, text="Dayandır", style="D.TButton", state="disabled", command=self.stop_lease); self.stop_btn.pack(side="right")
        self.conn_btn = ttk.Button(top, text="Qoşul", style="G.TButton", state="disabled", command=self.toggle_conn); self.conn_btn.pack(side="right", padx=8)
        box, self.ltree = self.tree(f, [("role", "Rol", 100, "w"), ("node", "Cihaz", 200, "w"), ("state", "Vəziyyət", 220, "w"), ("left", "Qalan vaxt", 130, "center"), ("paid", "Ödənib / depozit (kudos)", 190, "center")], 5)
        box.pack(fill="both", expand=True, padx=28, pady=(0, 10)); self.lempty = self.empty(box, "", "")
        self.ltree.bind("<<TreeviewSelect>>", lambda e: self.lease_sel())

        self.conn = tk.Frame(f, bg=CARD, highlightbackground=BD, highlightthickness=1)
        ch = tk.Frame(self.conn, bg=SIDE); ch.pack(fill="x")
        self.conn_title = self.lbl(ch, "", 10, True, "white", SIDE); self.conn_title.pack(side="left", padx=12, pady=7)
        nb = ttk.Notebook(self.conn); nb.pack(fill="both", expand=True, padx=1, pady=(0, 1)); self.conn_nb = nb

        tt = tk.Frame(nb, bg=CARD); nb.add(tt, text="Terminal")
        self.lbl(tt, "Bu cihazda əmr icra olunur — nəticəyə görə sən məsuliyyət daşıyırsan.", 9, fg=MU).pack(anchor="w", padx=10, pady=(8, 0))
        self.term_out = ScrolledText(tt, height=6, bg="#0b1b24", fg="#d8f0e8", insertbackground="#d8f0e8",
                                      font=("Consolas", 10), relief="flat", padx=10, pady=8, wrap="word", state="disabled")
        self.term_out.pack(fill="both", expand=True, padx=10, pady=8)
        bb = tk.Frame(tt, bg=CARD); bb.pack(fill="x", padx=10, pady=(0, 10))
        self.lbl(bb, "$", 11, True, fg=MU).pack(side="left")
        self.term_in = tk.StringVar(); e = ttk.Entry(bb, textvariable=self.term_in); e.pack(side="left", fill="x", expand=True, padx=8)
        e.bind("<Return>", lambda ev: self.run_term())
        self.term_run = ttk.Button(bb, text="İşə sal", style="A.TButton", command=self.run_term); self.term_run.pack(side="left")
        self.term_entry = e

        ts = tk.Frame(nb, bg=CARD); nb.add(ts, text="SSH")
        g = tk.Frame(ts, bg=CARD); g.pack(fill="x", padx=16, pady=(10, 6)); g.grid_columnconfigure(1, weight=1)
        self.ssh_status = self.lbl(g, "", 10, fg=MU); self.ssh_status.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.ssh_vals = {}
        for i, (k, label, masked) in enumerate((("host", "Server (host)", False), ("port", "Port", False),
                                                  ("user", "İstifadəçi adı", False), ("pw", "Şifrə", True),
                                                  ("link", "Qoşulma linki", False)), start=1):
            self.lbl(g, label, 9, fg=MU).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value="—"); self.ssh_vals[k] = var
            en = ttk.Entry(g, textvariable=var, state="readonly", show=("•" if masked else "")); en.grid(row=i, column=1, sticky="we", padx=8)
            if masked:
                self.ssh_pw_entry = en
                self.ssh_pw_btn = ttk.Button(g, text="Göstər", style="G.TButton", command=self.toggle_ssh_pw)
                self.ssh_pw_btn.grid(row=i, column=2, padx=2)
            else:
                ttk.Button(g, text="Kopyala", style="G.TButton", command=lambda v=var: self.clip(v.get(), "Kopyalandı")).grid(row=i, column=2, padx=2)
        self.lbl(ts, "Linki terminalına yapışdır (Enter) — SSH istifadəçi adı/şifrəni səndən soruşacaq, yuxarıdakı məlumatı yapışdır.",
                 9, fg=MU).pack(anchor="w", padx=16, pady=(0, 6))
        br = tk.Frame(ts, bg=CARD); br.pack(fill="x", padx=16, pady=(0, 6))
        ttk.Button(br, text="SSH əmrini kopyala", style="G.TButton", command=self.copy_ssh_cmd).pack(side="left")
        ttk.Button(br, text="Terminalda aç", style="A.TButton", command=self.open_ssh_terminal).pack(side="left", padx=8)
        self.lbl(ts, "Bu, provayderin kompüterində yaradılan müvəqqəti hesabdır. İcarə bitəndə avtomatik silinir.", 9, fg=MU).pack(anchor="w", padx=16, pady=(0, 6))
        self.ssh_show = False

        tl = tk.Frame(nb, bg=CARD); nb.add(tl, text="Canlı terminal")
        lh = tk.Frame(tl, bg=CARD); lh.pack(fill="x", padx=10, pady=(8, 0))
        self.live_status = self.lbl(lh, "Qoşulmayıb", 10, fg=MU); self.live_status.pack(side="left")
        self.live_btn = ttk.Button(lh, text="Bağlan", style="A.TButton", command=self.live_connect); self.live_btn.pack(side="right")
        self.live_out = ScrolledText(tl, height=6, bg="#0b1b24", fg="#d8f0e8", insertbackground="#d8f0e8",
                                      font=("Consolas", 10), relief="flat", padx=10, pady=8, wrap="word", state="disabled")
        self.live_out.pack(fill="both", expand=True, padx=10, pady=8)
        lb = tk.Frame(tl, bg=CARD); lb.pack(fill="x", padx=10, pady=(0, 4))
        self.lbl(lb, "$", 11, True, fg=MU).pack(side="left")
        self.live_in = tk.StringVar(); le = ttk.Entry(lb, textvariable=self.live_in, state="disabled"); le.pack(side="left", fill="x", expand=True, padx=8)
        le.bind("<Return>", lambda ev: self.live_send())
        self.live_send_btn = ttk.Button(lb, text="Göndər", style="A.TButton", state="disabled", command=self.live_send); self.live_send_btn.pack(side="left")
        self.live_entry = le
        if paramiko:
            self.lbl(tl, "Şifrəni yazmağa ehtiyac yoxdur — yuxarıdakı SSH hesabı ilə avtomatik daxil olur. Sadə sətir-sətir terminaldır.", 9, fg=MU).pack(anchor="w", padx=10, pady=(0, 8))
        else:
            self.lbl(tl, "Daxili canlı terminal üçün paramiko lazımdır: pip install paramiko. Hələlik SSH tabındakı \"Terminalda aç\" düyməsini istifadə et.", 9, fg=MU).pack(anchor="w", padx=10, pady=(0, 8))
            self.live_btn.config(state="disabled")

    def lease_sel(self):
        s = self.ltree.selection(); l = next((x for x in self.leases if s and x["id"] == s[0]), None)
        self.stop_btn.config(state="normal" if l and l["state"] == "active" else "disabled")
        can = bool(l and l["state"] == "active" and l["role"] == "buyer")
        self.conn_btn.config(state="normal" if can else "disabled")
        if not can and self.term_lease: self.close_conn()
        elif self.term_lease and l and l["id"] != self.term_lease: self.close_conn()

    def stop_lease(self):
        s = self.ltree.selection()
        if s: self.bg(lambda: self.api(f"/api/leases/{s[0]}/close", "POST"), lambda _: self.say("İcarə bağlandı"))

    def draw_leases(self):
        sel = self.ltree.selection(); self.ltree.delete(*self.ltree.get_children())
        a = sorted(self.leases, key=lambda x: (x["state"] != "active", -x["started"]))
        for x in a:
            on = x["state"] == "active"
            self.ltree.insert("", "end", iid=x["id"], tags=() if on else ("off",), values=("Alıcı" if x["role"] == "buyer" else "Provayder", x["node_id"],
                "Aktiv" if on else "Bağlandı: " + WHY.get(x["reason"], x["reason"]), fmt_left(x["left_sec"]) if on else "—", f"{x['paid']:.3f} / {x['escrow']:.3f}"))
        if sel and self.ltree.exists(sel[0]): self.ltree.selection_set(sel[0])
        self.lease_sel(); act = sum(1 for x in a if x["state"] == "active")
        row, bar, l, text = self.navs["leases"]; l.config(text=f"{text}  ({act})" if act else text)
        if a: self.lempty.place_forget()
        else: self.lempty.config(text="İcarə yoxdur\nBazardan cihaz götür və ya öz cihazını sat."); self.lempty.place(relx=.5, rely=.4, anchor="center")
        if self.term_lease and not any(x["id"] == self.term_lease and x["state"] == "active" for x in a): self.close_conn()
        elif self.term_lease: self.update_ssh_tab(next((x for x in a if x["id"] == self.term_lease), None))
        if self.pending_select_lease:
            l = next((x for x in a if x["id"] == self.pending_select_lease and x["state"] == "active"), None)
            if l: lid = self.pending_select_lease; self.pending_select_lease = None; self.open_conn(lid, tab=1)

    # ── Qoşulma paneli: Terminal + SSH + Canlı terminal ──
    def toggle_conn(self):
        s = self.ltree.selection()
        if not s: return
        if self.term_lease == s[0]: return self.close_conn()
        self.open_conn(s[0])

    def open_conn(self, lid, tab=0):
        l = next((x for x in self.leases if x["id"] == lid), None)
        if not l: return
        if self.ssh_connected: self.live_disconnect()
        if self.ltree.exists(lid): self.ltree.selection_set(lid)
        self.term_lease, self.term_shown, self.ssh_show = lid, set(), False
        self.ssh_pw_entry.config(show="•"); self.ssh_pw_btn.config(text="Göstər")
        self.conn_title.config(text=f"Qoşulma — {l['node_id']}")
        self.term_out.config(state="normal"); self.term_out.delete("1.0", "end")
        self.term_out.insert("end", "Bu kompüter kirayə götürdüyün cihazdır. Oradakı əmrlər sənin adından icra olunur.\n\n")
        self.term_out.config(state="disabled")
        self.live_closed()
        self.conn.pack(fill="both", expand=False, padx=28, pady=(0, 20))
        self.conn_btn.config(text="Bağla"); self.poll_term()
        self.update_ssh_tab(l); self.lease_sel()
        self.conn_nb.select(tab)
        if tab == 0: self.term_entry.focus_set()

    def close_conn(self):
        if self.ssh_connected: self.live_disconnect()
        self.term_lease = None; self.conn.pack_forget(); self.conn_btn.config(text="Qoşul")

    def term_write(self, text, tag=None):
        self.term_out.config(state="normal"); self.term_out.insert("end", text, tag); self.term_out.see("end"); self.term_out.config(state="disabled")

    def run_term(self):
        cmd = self.term_in.get().strip()
        if not cmd or not self.term_lease or self.term_busy: return
        self.term_in.set(""); lid = self.term_lease; self.term_write(f"$ {cmd}\n")
        self.term_busy = True; self.term_run.config(state="disabled")
        def reset(err=None):
            self.term_busy = False; self.term_run.config(state="normal")
            if err: self.term_write(f"[xəta: {err}]\n\n"); self.say(str(err), True)
        def work():
            try: r, err = self.api(f"/api/leases/{lid}/exec", "POST", {"cmd": cmd}), None
            except Exception as e: r, err = None, e
            self.q.put(lambda: reset(err))
        threading.Thread(target=work, daemon=True).start()

    def poll_term(self):
        if not self.term_lease: return
        lid = self.term_lease
        def work():
            try: return self.api(f"/api/leases/{lid}/exec")
            except Exception: return None
        def ok(rows):
            if rows is None or self.term_lease != lid: return
            for c in rows:
                if c["id"] in self.term_shown: continue
                if c["status"] in ("done", "timeout"):
                    out = (c.get("out") or "").rstrip("\n")
                    self.term_write((out + "\n" if out else "") + f"[kod: {c['rc'] if c['rc'] is not None else '—'}]\n\n")
                    self.term_shown.add(c["id"])
        threading.Thread(target=lambda: self.q.put(lambda r=work(): ok(r)), daemon=True).start()
        self.after(1800, self.poll_term)

    def clip(self, text, msg):
        if not text or text == "—": return self.say("Kopyalanacaq məlumat yoxdur", True)
        self.clipboard_clear(); self.clipboard_append(text); self.say(msg)

    def toggle_ssh_pw(self):
        self.ssh_show = not self.ssh_show; self.ssh_pw_entry.config(show="" if self.ssh_show else "•")
        self.ssh_pw_btn.config(text="Gizlət" if self.ssh_show else "Göstər")

    def update_ssh_tab(self, l):
        if not l: return
        ssh = l.get("ssh") or {"status": "none"}; st = ssh.get("status", "none")
        if st == "ready":
            self.ssh_status.config(text="✓ Hazırdır — aşağıdakı məlumatla qoşul.", fg="#17814f")
            host, port, user = ssh.get("host") or "—", ssh.get("port") or 22, ssh.get("username") or "—"
            self.ssh_vals["host"].set(host); self.ssh_vals["port"].set(str(port))
            self.ssh_vals["user"].set(user); self.ssh_vals["pw"].set(ssh.get("password") or "—")
            self.ssh_vals["link"].set(f"ssh://{user}@{host}:{port}")
        elif st == "failed":
            self.ssh_status.config(text="✗ Alınmadı: " + str(ssh.get("reason") or "naməlum xəta"), fg="#b3361f")
            for v in self.ssh_vals.values(): v.set("—")
        elif st == "closed":
            self.ssh_status.config(text="İcarə bağlandığı üçün bu hesab silinib.", fg=MU)
            for v in self.ssh_vals.values(): v.set("—")
        elif st == "pending":
            self.ssh_status.config(text="Provayderin cihazında SSH girişi hazırlanır…", fg=MU)
            for v in self.ssh_vals.values(): v.set("—")
        else:
            self.ssh_status.config(text="Bu icarə üçün SSH məlumatı yoxdur (provayderin proqramı köhnə ola bilər).", fg=MU)
            for v in self.ssh_vals.values(): v.set("—")

    def copy_ssh_cmd(self):
        h, p, u = self.ssh_vals["host"].get(), self.ssh_vals["port"].get(), self.ssh_vals["user"].get()
        if h in ("—", ""): return self.say("SSH məlumatı hazır deyil", True)
        self.clip(f"ssh {u}@{h}" + (f" -p {p}" if p not in ("22", "") else ""), "SSH əmri kopyalandı")

    def open_ssh_terminal(self):
        h, p, u = self.ssh_vals["host"].get(), self.ssh_vals["port"].get(), self.ssh_vals["user"].get()
        if h in ("—", ""): return self.say("SSH məlumatı hazır deyil", True)
        cmd = f"ssh {u}@{h}" + (f" -p {p}" if p not in ("22", "") else "")
        try:
            if os.name == "nt":
                subprocess.Popen(f'start "IdlePool SSH" cmd /k {cmd}', shell=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"'])
            else:
                for term in (["x-terminal-emulator", "-e", cmd], ["gnome-terminal", "--", "bash", "-c", cmd + "; exec bash"], ["xterm", "-e", cmd]):
                    try:
                        subprocess.Popen(term); break
                    except FileNotFoundError:
                        continue
                else:
                    raise FileNotFoundError
            self.say("Terminal açıldı")
        except Exception:
            self.clip(cmd, "Terminal tapılmadı, əmr kopyalandı: " + cmd)

    # ── Canlı SSH terminalı (daxili, şifrə əllə yazılmır) ──
    def live_connect(self):
        if not paramiko: return self.say("paramiko quraşdırılmayıb: pip install paramiko", True)
        if self.ssh_connected: return
        host, port, user, pw = (self.ssh_vals[k].get() for k in ("host", "port", "user", "pw"))
        if host in ("—", "") or user in ("—", "") or pw in ("—", ""):
            return self.say("Əvvəlcə SSH tabında məlumatın hazır olmasını gözlə.", True)
        self.live_status.config(text="Qoşulur…", fg=MU); self.live_btn.config(state="disabled")
        lid = self.term_lease
        def work():
            try:
                cl = paramiko.SSHClient(); cl.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                cl.connect(host, port=int(port or 22), username=user, password=pw, timeout=10,
                           banner_timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)
                ch = cl.invoke_shell(term="xterm")
                self.q.put(lambda: self.live_ready(cl, ch, lid))
                threading.Thread(target=self.live_reader, args=(ch, lid), daemon=True).start()
            except Exception as e:
                self.q.put(lambda err=e: self.live_fail(str(err)))
        threading.Thread(target=work, daemon=True).start()

    def _alive(self):
        try: return bool(self.winfo_exists())
        except tk.TclError: return False

    def live_ready(self, cl, ch, lid):
        if not self._alive():
            try: cl.close()
            except Exception: pass
            return
        if self.term_lease != lid: 
            try: cl.close()
            except Exception: pass
            return
        self.ssh_client, self.ssh_channel, self.ssh_connected = cl, ch, True
        self.live_status.config(text="✓ Qoşuldu", fg="#17814f")
        self.live_btn.config(text="Bağlantını kəs", state="normal", command=self.live_disconnect)
        self.live_entry.config(state="normal"); self.live_send_btn.config(state="normal"); self.live_entry.focus_set()

    def live_fail(self, msg):
        if not self._alive(): return
        self.live_status.config(text="✗ Alınmadı: " + msg[:140], fg="#b3361f")
        self.live_btn.config(text="Bağlan", state="normal" if paramiko else "disabled", command=self.live_connect)

    def live_reader(self, ch, lid):
        try:
            while True:
                data = ch.recv(4096)
                if not data: break
                self.q.put(lambda t=data.decode(errors="replace"): self.live_write(t, lid))
        except Exception:
            pass
        self.q.put(lambda: self.live_closed(lid))

    def live_write(self, text, lid):
        if not self._alive() or self.term_lease != lid or not self.ssh_connected: return
        self.live_out.config(state="normal"); self.live_out.insert("end", text); self.live_out.see("end"); self.live_out.config(state="disabled")

    def live_send(self):
        if not self.ssh_connected or not self.ssh_channel: return
        line = self.live_in.get(); self.live_in.set("")
        try: self.ssh_channel.send(line + "\n")
        except Exception as e: self.say(str(e), True); self.live_closed(self.term_lease)

    def live_disconnect(self):
        try:
            if self.ssh_channel: self.ssh_channel.close()
        except Exception: pass
        try:
            if self.ssh_client: self.ssh_client.close()
        except Exception: pass
        self.live_closed(self.term_lease)

    def live_closed(self, lid=None):
        self.ssh_connected = False; self.ssh_client = self.ssh_channel = None
        if not self._alive(): return
        if lid is not None and lid != self.term_lease: return
        self.live_status.config(text="Qoşulmayıb", fg=MU)
        self.live_btn.config(text="Bağlan", state="normal" if paramiko else "disabled", command=self.live_connect)
        self.live_entry.config(state="disabled"); self.live_send_btn.config(state="disabled")

    # ── Cihaz ──
    def build_device(self):
        f = self.pages["device"]; self.head(f, "Cihazımı sat", "Bu kompüterin boş gücünü bazara çıxar və kudos qazan.")
        self.build_machine(f, True)
        c = self.card(f); c.pack(fill="x", padx=28); self.lbl(c, "Təklifini seç", 12, True).pack(anchor="w", padx=18, pady=(16, 8))
        g = tk.Frame(c, bg=CARD); g.pack(fill="x", padx=18); self.dv = {}
        for i, (k, name, v) in enumerate((("ram", "RAM (GB)", "4"), ("disk", "Disk (GB)", "50"), ("cores", "Nüvə", "2"), ("price", "Qiymət (kudos/saat)", "5"))):
            cell = tk.Frame(g, bg=CARD); cell.grid(row=0, column=i, padx=(0, 14), sticky="w"); self.lbl(cell, name, 9, fg=MU).pack(anchor="w")
            self.dv[k] = tk.StringVar(value=v); self.dv[k].trace_add("write", lambda *a: self.draw_cmd()); ttk.Entry(cell, textvariable=self.dv[k], width=13).pack()
        self.dallow = tk.BooleanVar(value=True); self.dallow.trace_add("write", lambda *a: self.draw_cmd())
        ttk.Checkbutton(c, text="Alıcıların bu cihazda uzaq əmr icra etməsinə icazə ver", variable=self.dallow, style="C.TCheckbutton").pack(anchor="w", padx=18, pady=(10, 0))
        self.lbl(c, "İcazə versən, cihazını kirayə alan şəxs orada əmrlər işlədə biləcək. Yalnız etibar etdiyin alıcılara icazə ver.", 9, fg=MU).pack(anchor="w", padx=18, pady=(0, 4))
        self.dssh = tk.BooleanVar(value=True); self.dssh.trace_add("write", lambda *a: self.draw_cmd())
        ttk.Checkbutton(c, text="İcarə zamanı alıcı üçün SSH girişi yarat", variable=self.dssh, style="C.TCheckbutton").pack(anchor="w", padx=18, pady=(6, 0))
        self.lbl(c, "Yalnız Windows-da işləyir və admin hüququ istəyir.", 9, fg=MU).pack(anchor="w", padx=18, pady=(0, 6))
        sg = tk.Frame(c, bg=CARD); sg.pack(fill="x", padx=18, pady=(0, 4))
        for i, (k, name) in enumerate((("suser", "SSH istifadəçi adı (istəyə bağlı)"), ("spw", "SSH şifrəsi (istəyə bağlı)"))):
            cell = tk.Frame(sg, bg=CARD); cell.grid(row=0, column=i, padx=(0, 14), sticky="w")
            self.lbl(cell, name, 9, fg=MU).pack(anchor="w")
            self.dv[k] = tk.StringVar(value=""); self.dv[k].trace_add("write", lambda *a: self.draw_cmd())
            ttk.Entry(cell, textvariable=self.dv[k], width=20, show=("•" if k == "spw" else "")).pack()
        self.lbl(c, "Boş saxlasan hər icarə üçün təsadüfi, müvəqqəti hesab yaradılır (icarə bitəndə silinir). İkisini də doldursan, bu sabit hesab hər icarədə təkrar istifadə olunur və silinmir.",
                 9, fg=MU, justify="left", anchor="w", wraplength=760).pack(anchor="w", padx=18, pady=(2, 4), fill="x")
        self.lbl(c, "Aşağıdakı əmri satmaq istədiyin kompüterdə PowerShell-də (administrator kimi) işə sal. Cihaz bazarda görünəcək.", 10, fg=MU).pack(anchor="w", padx=18, pady=(10, 6))
        self.cmd = tk.Text(c, height=3, wrap="word", font=("Consolas", 10), bg=PANE, fg=TX, relief="flat", padx=10, pady=8, highlightbackground=BD, highlightthickness=1)
        self.cmd.pack(fill="x", padx=18)
        br = tk.Frame(c, bg=CARD); br.pack(anchor="w", padx=18, pady=16)
        self.copy_btn = ttk.Button(br, text="Əmri kopyala", style="A.TButton", command=self.copy_cmd); self.copy_btn.pack(side="left")

    def draw_cmd(self):
        ok = bool(self.me); g = lambda k: self.dv[k].get().strip() or "0"
        su, sp = self.dv["suser"].get().strip(), self.dv["spw"].get().strip()
        extra = ("" if self.dallow.get() else " --no-exec") + ("" if self.dssh.get() else " --no-ssh")
        if su: extra += f' --ssh-user "{su}"'
        if sp: extra += f' --ssh-password "{sp}"'
        t = (f"{agent_command()} agent --key {self.me['key']} --ram-gb {g('ram')} --disk-gb {g('disk')} --cores {g('cores')} --price {g('price')}" + extra
             if ok else "Əvvəl hesaba daxil ol.")
        self.cmd.config(state="normal"); self.cmd.delete("1.0", "end"); self.cmd.insert("1.0", t); self.cmd.config(state="disabled")
        self.copy_btn.config(state="normal" if ok else "disabled")

    def copy_cmd(self):
        self.clipboard_clear(); self.clipboard_append(self.cmd.get("1.0", "end").strip()); self.say("Əmr kopyalandı")

    # ── Hesab ──
    def build_account(self):
        f = self.pages["account"]; self.head(f, "Hesab", "Daxil ol və ya yeni hesab aç.")
        self.acc_out = tk.Frame(f, bg=PANE); self.acc_in = tk.Frame(f, bg=PANE)
        c1 = self.card(self.acc_out); c1.pack(side="left", fill="both", expand=True, padx=(28, 7), anchor="n")
        self.lbl(c1, "Yeni hesab", 12, True).pack(anchor="w", padx=18, pady=(16, 2)); self.lbl(c1, "Başlanğıc üçün 100 kudos verilir.", 10, fg=MU).pack(anchor="w", padx=18)
        self.nm = tk.StringVar(); ttk.Entry(c1, textvariable=self.nm).pack(fill="x", padx=18, pady=10)
        ttk.Button(c1, text="Qeydiyyatdan keç", style="A.TButton", command=self.register).pack(anchor="w", padx=18, pady=(0, 16))
        c2 = self.card(self.acc_out); c2.pack(side="left", fill="both", expand=True, padx=(7, 28), anchor="n")
        self.lbl(c2, "Açarla daxil ol", 12, True).pack(anchor="w", padx=18, pady=(16, 2)); self.lbl(c2, "Qeydiyyatda verilən açarı yapışdır.", 10, fg=MU).pack(anchor="w", padx=18)
        self.ky = tk.StringVar(); ttk.Entry(c2, textvariable=self.ky).pack(fill="x", padx=18, pady=10)
        ttk.Button(c2, text="Daxil ol", style="G.TButton", command=self.login).pack(anchor="w", padx=18, pady=(0, 16))
        c = self.card(self.acc_in); c.pack(fill="x", padx=28)
        self.a_name = self.lbl(c, "", 15, True); self.a_name.pack(anchor="w", padx=18, pady=(16, 0))
        self.lbl(c, "Balans", 9, fg=MU).pack(anchor="w", padx=18, pady=(8, 0)); self.a_bal = self.lbl(c, "", 20, True, GOLDD); self.a_bal.pack(anchor="w", padx=18)
        self.lbl(c, "Gizli açarın", 9, fg=MU).pack(anchor="w", padx=18, pady=(12, 4))
        self.a_key = tk.Label(c, text="", font=("Consolas", 10), bg=PANE, fg=TX, anchor="w", padx=10, pady=8); self.a_key.pack(fill="x", padx=18)
        r = tk.Frame(c, bg=CARD); r.pack(anchor="w", padx=18, pady=16)
        self.show_btn = ttk.Button(r, text="Göstər", style="G.TButton", command=self.toggle_key); self.show_btn.pack(side="left")
        ttk.Button(r, text="Kopyala", style="G.TButton", command=self.copy_key).pack(side="left", padx=8)
        ttk.Button(r, text="Çıxış", style="D.TButton", command=self.logout).pack(side="left")

    def register(self):
        name = self.nm.get().strip() or "istifadəçi"
        self.bg(lambda: self.api("/api/register", "POST", {"name": name}, key=""), lambda j: (self.set_key(j["key"]), self.say("Hesab yaradıldı. Açarını saxla.")))

    def login(self):
        k = self.ky.get().strip()
        self.bg(lambda: self.api("/api/me", key=k), lambda _: (self.set_key(k), self.ky.set(""), self.say("Daxil oldun")))

    def logout(self):
        self.set_key(""); self.me = None; self.orders, self.leases, self.show_key = [], [], False; self.draw_all(); self.say("Çıxış etdin")

    def toggle_key(self): self.show_key = not self.show_key; self.draw_account()

    def copy_key(self):
        if self.me: self.clipboard_clear(); self.clipboard_append(self.me["key"]); self.say("Açar kopyalandı")

    def draw_account(self):
        on = bool(self.me); self.acc_out.pack_forget(); self.acc_in.pack_forget()
        (self.acc_in if on else self.acc_out).pack(fill="x")
        if on:
            self.a_name.config(text=self.me["name"]); self.a_bal.config(text=f"{self.me['kudos']:.2f} kudos")
            self.a_key.config(text=self.me["key"] if self.show_key else "•" * 22); self.show_btn.config(text="Gizlət" if self.show_key else "Göstər")

    # ── yeniləmə ──
    def refresh(self):
        if self.busy: return
        self.busy = True; key = self.key
        def work():
            out = {}
            try: out["nodes"] = self.api("/nodes")
            except Exception: out["offline"] = True
            else:
                if key:
                    try:
                        out["me"] = self.api("/api/me", key=key); out["orders"] = self.api("/api/orders", key=key); out["leases"] = self.api("/api/leases", key=key)
                    except RuntimeError as e: out["authfail"] = "açar" in str(e)
                    except Exception: pass
            self.q.put(lambda: self.apply(out))
        threading.Thread(target=work, daemon=True).start()

    def apply(self, out):
        try:
            if not self.winfo_exists(): return
        except tk.TclError:
            return
        self.busy = False; self.online = not out.get("offline")
        if "nodes" in out: self.nodes = out["nodes"]
        if out.get("authfail"): self.set_key("")
        if "me" in out: self.me, self.orders, self.leases = out["me"], out["orders"], out["leases"]
        if not self.key: self.me, self.orders, self.leases = None, [], []
        self.draw_all()

    def draw_all(self):
        self.draw_market(); self.draw_orders(); self.draw_leases(); self.draw_cmd(); self.draw_account()
        self.me_name.config(text=self.me["name"] if self.me else "Daxil olmamısan"); self.me_k.config(text=f"{self.me['kudos']:.2f} kudos" if self.me else "—")
        self.s_state.config(text="● Yerli server işləyir" if self.online else "● Server cavab vermir", fg="#4fd08e" if self.online else "#ff8f7a")
        self.s_cnt.config(text=f"{len(self.nodes)} cihaz onlayn"); self.s_upd.config(text=f"Yeniləndi {time.strftime('%H:%M:%S')} · F5")


# ───────────────────────── SERVER & APP ─────────────────────────
def run_app(host, port):
    import uvicorn
    from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel, Field

    L = threading.RLock()
    DB = {"users": {}, "orders": {}, "bids": {}, "leases": {}}
    if DATA_FILE.exists():
        try:
            text = DATA_FILE.read_text()
            if text.strip():
                loaded = json.loads(text)
                if isinstance(loaded, dict): DB.update(loaded)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            try:
                backup = DATA_FILE.with_name(DATA_FILE.stem + f".corrupt-{int(time.time())}" + DATA_FILE.suffix)
                DATA_FILE.replace(backup)
                print(f"Xəbərdarlıq: {DATA_FILE.name} oxuna bilmədi ({e}); köhnə fayl {backup.name} adı ilə saxlanıldı, boş verilənlər bazası ilə başlanılır.")
            except OSError:
                print(f"Xəbərdarlıq: {DATA_FILE.name} oxuna bilmədi ({e}); boş verilənlər bazası ilə başlanılır.")
    NODES, OUTBOX = {}, {}

    def save():
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATA_FILE.with_suffix(".tmp"); tmp.write_text(json.dumps(DB)); tmp.replace(DATA_FILE)
    def uid(p): return p + "-" + uuid.uuid4().hex[:8]
    def user_by_key(key):
        return next((u for u in DB["users"].values() if u["key"] == key and key), None)
    def need_user(key):
        u = user_by_key(key)
        if not u: raise HTTPException(401, "Yanlış və ya boş açar")
        return u
    def online(n): return time.time() - n["last_seen"] < ONLINE_SEC
    def busy(nid): return any(l["node_id"] == nid and l["state"] == "active" for l in DB["leases"].values())
    def push(nid, msg): OUTBOX.setdefault(nid, []).append(msg)

    def settle(l, now):
        end = min(now, l["ends"]); dt = max(0.0, end - l["settled_at"])
        amt = min(l["price"] * dt / 3600, l["escrow"] - l["paid"])
        l["settled_at"] = end
        if amt > 0:
            l["paid"] += amt
            DB["users"][l["provider"]]["kudos"] += amt

    def close_lease(l, reason, now):
        settle(l, now)
        DB["users"][l["buyer"]]["kudos"] += l["escrow"] - l["paid"]
        l["state"], l["reason"] = "closed", reason
        if l.get("ssh", {}).get("status") == "ready":
            l["ssh"] = {"status": "closed"}
        push(l["node_id"], {"type": "lease_closed", "lease": l["id"], "reason": reason})

    def tick():
        now = time.time()
        for n in NODES.values():
            n["total"] += 1; n["up"] += 1 if online(n) else 0
        for l in DB["leases"].values():
            for c in l.get("cmds", []):
                if c["status"] in ("queued", "running") and now - c["sent"] > EXEC_TIMEOUT:
                    c["status"], c["out"], c["rc"] = "timeout", (c.get("out", "") + "\n[vaxt bitdi, cihab cavab vermədi]").strip(), None
            if l["state"] != "active": continue
            n = NODES.get(l["node_id"])
            if not n or not online(n): close_lease(l, "provider_offline", now)
            elif now >= l["ends"]: close_lease(l, "completed", now)
            else: settle(l, now)
        save()

    @asynccontextmanager
    async def lifespan(app):
        async def loop():
            while True:
                await asyncio.sleep(5)
                with L: tick()
        task = asyncio.create_task(loop())
        app.state.lease_task = task
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="IdlePool Desktop", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        details = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details, "path": request.url.path})

    class Reg(BaseModel): name: str
    class OrderIn(BaseModel):
        ram_gb: float = Field(gt=0, allow_inf_nan=False)
        disk_gb: float = Field(gt=0, allow_inf_nan=False)
        cores: int = Field(gt=0)
        hours: float = Field(gt=0, allow_inf_nan=False)
        max_price: float = Field(gt=0, allow_inf_nan=False)
    class Accept(BaseModel): bid_id: str
    class ExecIn(BaseModel): cmd: str = Field(min_length=1, max_length=4000)

    @app.get("/", response_class=HTMLResponse)
    def index(): return "IdlePool server isleyir"

    @app.post("/api/register")
    def register(r: Reg):
        with L:
            u = {"id": uid("u"), "name": r.name[:30], "key": secrets.token_urlsafe(16), "kudos": START_KUDOS}
            DB["users"][u["id"]] = u; save(); return u

    @app.get("/api/me")
    def me(x_key: str = Header("")):
        with L: return need_user(x_key)

    @app.get("/api/agent-command")
    def agent_command():
        return {"command": agent_command()}

    @app.get("/nodes")
    def nodes():
        with L:
            return sorted([{
                "node_id": n["node_id"],
                "hostname": n["specs"]["hostname"],
                "ram_gb": n["offer"]["ram_gb"],
                "disk_gb": n["offer"]["disk_gb"],
                "cores": n["offer"]["cores"],
                "price": n["offer"]["price_per_hour"],
                "uptime": round(n["up"] / max(1, n["total"]), 3),
                "owned": bool(n["owner"]),
                "busy": busy(n["node_id"])
            } for n in NODES.values() if online(n)], key=lambda n: n["price"])

    @app.post("/api/orders")
    def make_order(o: OrderIn, x_key: str = Header("")):
        with L:
            u = need_user(x_key)
            order = {"id": uid("o"), "buyer": u["id"], **o.model_dump(), "state": "open", "created": time.time()}
            DB["orders"][order["id"]] = order; bids = []
            for n in NODES.values():
                of = n["offer"]
                if (n["owner"] and n["owner"] != u["id"] and online(n) and not busy(n["node_id"])
                        and of["ram_gb"] >= o.ram_gb and of["disk_gb"] >= o.disk_gb and of["cores"] >= o.cores
                        and of["price_per_hour"] <= o.max_price):
                    b = {"id": uid("b"), "order_id": order["id"], "node_id": n["node_id"], "price": of["price_per_hour"], "state": "open"}
                    DB["bids"][b["id"]] = b; bids.append(b)
            save(); return {"order": order, "bids": sorted(bids, key=lambda b: b["price"])}

    @app.get("/api/orders")
    def my_orders(x_key: str = Header("")):
        with L:
            u = need_user(x_key)
            return [{**o, "bids": [b for b in DB["bids"].values() if b["order_id"] == o["id"]]}
                    for o in DB["orders"].values() if o["buyer"] == u["id"]][-30:]

    @app.post("/api/orders/{oid}/accept")
    def accept(oid: str, a: Accept, x_key: str = Header("")):
        with L:
            u = need_user(x_key); order = DB["orders"].get(oid); b = DB["bids"].get(a.bid_id)
            if not order or order["buyer"] != u["id"] or order["state"] != "open": raise HTTPException(400, "Sifariş uyğun deyil")
            if not b or b["order_id"] != oid or b["state"] != "open": raise HTTPException(400, "Təklif uyğun deyil")
            n = NODES.get(b["node_id"])
            if not n or not online(n) or busy(n["node_id"]): raise HTTPException(409, "Düyün artıq məşğul və ya oflayndır")
            total_cost = b["price"] * order["hours"]
            end_time = time.time() + order["hours"] * 3600
            if not math.isfinite(total_cost) or not math.isfinite(end_time):
                raise HTTPException(422, "Sifariş dəyərləri dəstəklənən həddi aşır")
            cost = round(total_cost, 4)
            if u["kudos"] < cost: raise HTTPException(402, f"Kudos çatmır: lazım {cost}, var {round(u['kudos'], 2)}")
            u["kudos"] -= cost; now = time.time()
            l = {
                "id": uid("l"), "order_id": oid, "bid_id": b["id"], "buyer": u["id"], "provider": n["owner"],
                "node_id": n["node_id"], "price": b["price"], "escrow": cost, "paid": 0.0,
                "started": now, "settled_at": now, "ends": end_time,
                "state": "active", "reason": "", "cmds": [], "ssh": {"status": "pending"}
            }
            DB["leases"][l["id"]] = l; order["state"] = "matched"
            for x in DB["bids"].values():
                if x["order_id"] == oid: x["state"] = "won" if x is b else "lost"
            push(n["node_id"], {"type": "lease_started", "lease": l["id"], "hours": order["hours"],
                                 "ram_gb": order["ram_gb"], "disk_gb": order["disk_gb"], "cores": order["cores"]})
            save(); return l

    @app.get("/api/leases")
    def my_leases(x_key: str = Header("")):
        with L:
            u = need_user(x_key); now = time.time(); out = []
            for l in DB["leases"].values():
                if u["id"] in (l["buyer"], l["provider"]):
                    out.append({**l, "role": "buyer" if l["buyer"] == u["id"] else "provider", "left_sec": max(0, l["ends"] - now),
                                "ssh": l.get("ssh") or {"status": "none"}})
            return out[-30:]

    @app.post("/api/leases/{lid}/close")
    def close(lid: str, x_key: str = Header("")):
        with L:
            u = need_user(x_key); l = DB["leases"].get(lid)
            if not l or u["id"] not in (l["buyer"], l["provider"]) or l["state"] != "active": raise HTTPException(400, "İcarə uyğun deyil")
            close_lease(l, "closed_by_" + ("buyer" if l["buyer"] == u["id"] else "provider"), time.time()); save(); return l

    @app.post("/api/leases/{lid}/exec")
    def exec_cmd(lid: str, e: ExecIn, x_key: str = Header("")):
        with L:
            u = need_user(x_key); l = DB["leases"].get(lid)
            if not l or l["buyer"] != u["id"]: raise HTTPException(403, "Bu icarə sənə aid deyil")
            if l["state"] != "active": raise HTTPException(400, "İcarə artıq aktiv deyil")
            n = NODES.get(l["node_id"])
            if not n or not online(n): raise HTTPException(409, "Cihaz oflayndır")
            c = {"id": uid("x"), "cmd": e.cmd.strip(), "status": "queued", "out": "", "rc": None, "sent": time.time()}
            l.setdefault("cmds", []).append(c); l["cmds"] = l["cmds"][-50:]
            push(l["node_id"], {"type": "exec", "lease": lid, "cmd_id": c["id"], "cmd": c["cmd"]})
            save(); return c

    @app.get("/api/leases/{lid}/exec")
    def exec_list(lid: str, x_key: str = Header("")):
        with L:
            u = need_user(x_key); l = DB["leases"].get(lid)
            if not l or u["id"] not in (l["buyer"], l["provider"]): raise HTTPException(403, "Bu icarə sənə aid deyil")
            return l.get("cmds", [])

    @app.websocket("/ws/agent")
    async def agent_ws(ws: WebSocket):
        await ws.accept(); nid = None
        async def flusher():
            while True:
                await asyncio.sleep(1)
                if nid:
                    with L: msgs = OUTBOX.pop(nid, [])
                    for m in msgs: await ws.send_json(m)
        task = asyncio.create_task(flusher())
        try:
            while True:
                m = await ws.receive_json()
                with L:
                    if m["type"] == "hello":
                        nid = m["node_id"]; u = user_by_key(m.get("key", "")); old = NODES.get(nid, {})
                        NODES[nid] = {
                            "node_id": nid, "specs": m["specs"], "offer": m["offer"], "metrics": {},
                            "last_seen": time.time(), "owner": u["id"] if u else None,
                            "up": old.get("up", 0), "total": old.get("total", 0)
                        }
                        await ws.send_json({"type": "ok", "owner": u["name"] if u else None})
                    elif m["type"] == "heartbeat" and nid in NODES:
                        NODES[nid]["metrics"] = m["metrics"]; NODES[nid]["last_seen"] = time.time()
                    elif m["type"] == "exec_result":
                        l = DB["leases"].get(m.get("lease", ""))
                        if l:
                            for c in l.get("cmds", []):
                                if c["id"] == m.get("cmd_id"):
                                    c["status"] = "done"; c["out"] = (m.get("out") or "")[:EXEC_MAX_OUT]; c["rc"] = m.get("rc")
                            save()
                    elif m["type"] == "ssh_ready":
                        l = DB["leases"].get(m.get("lease", ""))
                        if l and l["state"] == "active":
                            l["ssh"] = {"status": "ready", "host": m.get("host"), "port": m.get("port", 22),
                                        "username": m.get("username"), "password": m.get("password")}
                            save()
                    elif m["type"] == "ssh_failed":
                        l = DB["leases"].get(m.get("lease", ""))
                        if l and l["state"] == "active":
                            l["ssh"] = {"status": "failed", "reason": m.get("reason", "")}
                            save()
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()

    # Start the local API before opening the desktop window.
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    try:
        deadline = time.monotonic() + 15
        while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError(f"Yerli server başlaya bilmədi: {host}:{port} ünvanı istifadə olunur və ya bloklanıb.")

        try:
            import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        app = IdlePoolApp(host, port)
        app.mainloop()
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)

# ───────────────────────── AGENT ─────────────────────────
def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("dəyər sıfırdan böyük və sonlu rəqəm olmalıdır")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("dəyər müsbət tam ədəd olmalıdır")
    return number


def win_outbound_ip():
    """Bu kompüterin yerli şəbəkə IP ünvanını tapır (internetə çıxış yoxlanmır, paket göndərilmir)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    except OSError:
        try: return socket.gethostbyname(socket.gethostname())
        except OSError: return "127.0.0.1"
    finally:
        s.close()


def win_gen_password(n=16):
    rnd = secrets.SystemRandom()
    core = [rnd.choice(string.ascii_uppercase), rnd.choice(string.ascii_lowercase), rnd.choice(string.digits), rnd.choice("!@#$%&*?-_")]
    core += [rnd.choice(string.ascii_letters + string.digits) for _ in range(max(0, n - len(core)))]
    rnd.shuffle(core); return "".join(core)


def win_run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=0x08000000 if os.name == "nt" else 0)


def win_ensure_sshd():
    """Windows OpenSSH Server-i (varsa) işə salır, yoxdursa quraşdırmağa çalışır. Admin hüququ lazımdır."""
    try:
        q = win_run(["sc", "query", "sshd"])
        if "RUNNING" in q.stdout:
            return True, ""
        if q.returncode != 0 or "does not exist" in q.stdout.lower():
            r = win_run(["powershell", "-NoProfile", "-Command",
                         "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0"], timeout=180)
            if r.returncode != 0:
                return False, ("OpenSSH Server quraşdırıla bilmədi: " + (r.stderr or r.stdout).strip())[:220]
        win_run(["powershell", "-NoProfile", "-Command", "Set-Service -Name sshd -StartupType Automatic"])
        win_run(["powershell", "-NoProfile", "-Command", "Start-Service sshd"], timeout=40)
        try:
            win_run(["powershell", "-NoProfile", "-Command",
                     "if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) "
                     "{ New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' "
                     "-Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 }"])
        except Exception:
            pass
        q2 = win_run(["sc", "query", "sshd"])
        return ("RUNNING" in q2.stdout), ("" if "RUNNING" in q2.stdout else "sshd servisi başlaya bilmədi")
    except Exception as e:
        return False, str(e)[:220]


def win_create_temp_user(username, password):
    r = win_run(["net", "user", username, password, "/add", "/expires:never", "/comment:IdlePool müvəqqəti SSH hesabı"])
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:220]
    win_run(["net", "localgroup", "Users", username, "/add"])
    return True, ""


def win_ensure_user(username, password):
    """Hesab mövcuddursa şifrəni ona uyğunlaşdırır (sahibin özü təyin etdiyi sabit hesab), yoxdursa yaradır."""
    q = win_run(["net", "user", username])
    if q.returncode == 0:
        r = win_run(["net", "user", username, password])
        if r.returncode != 0:
            return False, (r.stderr or r.stdout).strip()[:220]
        return True, ""
    return win_create_temp_user(username, password)


def win_delete_temp_user(username):
    try: win_run(["net", "user", username, "/delete"], timeout=20)
    except Exception: pass


def run_agent(a):
    import psutil, websockets
    AGENT_EXEC_TIMEOUT = 55
    id_file = Path.home() / ".idlepool_id"
    root = Path.home().anchor or "/"

    def node_id():
        if id_file.exists(): return id_file.read_text().strip()
        nid = "n-" + uuid.uuid4().hex[:8]; id_file.write_text(nid); return nid
    def specs():
        d = psutil.disk_usage(root)
        return {
            "hostname": socket.gethostname(), "os": f"{platform.system()} {platform.release()}",
            "cpu_logical": psutil.cpu_count(logical=True),
            "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
            "disk_total_gb": round(d.total / 1e9, 1), "disk_free_gb": round(d.free / 1e9, 1)
        }
    def metrics():
        b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_available_gb": round(psutil.virtual_memory().available / 1e9, 2),
            "disk_free_gb": round(psutil.disk_usage(root).free / 1e9, 1),
            "battery": b.percent if b else None
        }

    async def loop():
        s = specs()
        offer = {
            "ram_gb": round(min(a.ram_gb, s["ram_total_gb"] * 0.8), 2),
            "disk_gb": round(min(a.disk_gb, s["disk_free_gb"] * 0.8), 1),
            "cores": min(a.cores, s["cpu_logical"]),
            "price_per_hour": a.price
        }
        ssh_accounts = {}
        while True:
            try:
                async with websockets.connect(a.server) as ws:
                    await ws.send(json.dumps({
                        "type": "hello", "node_id": node_id(), "key": a.key, "specs": s, "offer": offer
                    }))
                    ok = json.loads(await ws.recv())
                    print(f"Düyün {node_id()} onlayn. Təklif: {offer}")
                    print("Sahib:", ok.get("owner") or "YOXDUR (açar səhvdir — təklif verilməyəcək)")
                    async def hb():
                        while True:
                            await ws.send(json.dumps({"type": "heartbeat", "metrics": metrics()}))
                            await asyncio.sleep(a.interval)
                    async def run_cmd(lease, cmd_id, cmd):
                        if not a.allow_exec:
                            await ws.send(json.dumps({"type": "exec_result", "lease": lease, "cmd_id": cmd_id,
                                                        "out": "Bu cihaz uzaq əmrləri qəbul etmir (--no-exec).", "rc": -1}))
                            return
                        print(f"▶ Əmr icra olunur ({lease}): {cmd}")
                        try:
                            proc = await asyncio.create_subprocess_shell(
                                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                            try:
                                out, _ = await asyncio.wait_for(proc.communicate(), timeout=AGENT_EXEC_TIMEOUT)
                                rc = proc.returncode
                            except asyncio.TimeoutError:
                                proc.kill(); await proc.wait()
                                out, rc = "[vaxt bitdi, əmr dayandırıldı]".encode(), -1
                        except Exception as e:
                            out, rc = str(e).encode(), -1
                        text = out.decode(errors="replace")[:20000]
                        await ws.send(json.dumps({"type": "exec_result", "lease": lease, "cmd_id": cmd_id, "out": text, "rc": rc}))
                        print(f"■ Əmr bitdi ({lease}), kod {rc}")
                    async def provision_ssh(lease):
                        if not a.enable_ssh:
                            await ws.send(json.dumps({"type": "ssh_failed", "lease": lease, "reason": "Bu cihaz SSH girişini söndürüb (--no-ssh)."}))
                            return
                        if os.name != "nt":
                            await ws.send(json.dumps({"type": "ssh_failed", "lease": lease, "reason": "SSH girişi hazırda yalnız Windows agentində avtomatik qurulur."}))
                            return
                        ev = asyncio.get_running_loop()
                        ok, reason = await ev.run_in_executor(None, win_ensure_sshd)
                        if not ok:
                            await ws.send(json.dumps({"type": "ssh_failed", "lease": lease,
                                "reason": "OpenSSH Server hazır deyil: " + reason + " (agenti administrator kimi işə sal)"}))
                            return
                        fixed_user = bool(a.ssh_user)
                        username = a.ssh_user or ("idlepool_" + lease.split("-")[-1][:8])
                        password = a.ssh_password or win_gen_password()
                        ok2, reason2 = await ev.run_in_executor(None, win_ensure_user, username, password)
                        if not ok2:
                            await ws.send(json.dumps({"type": "ssh_failed", "lease": lease,
                                "reason": "İstifadəçi hazırlana bilmədi: " + reason2 + " (agenti administrator kimi işə sal)"}))
                            return
                        if not fixed_user:
                            ssh_accounts[lease] = username  # yalnız müvəqqəti hesablar icarə bitəndə silinir
                        host = win_outbound_ip()
                        await ws.send(json.dumps({"type": "ssh_ready", "lease": lease, "host": host, "port": 22,
                                                    "username": username, "password": password}))
                        print(f"🔑 SSH hazırdır ({lease}): {username}@{host}")
                    async def rx():
                        async for raw in ws:
                            m = json.loads(raw)
                            if m["type"] == "lease_started":
                                print(f"▶ İcarə başladı {m['lease']}: {m['ram_gb']} GB, {m['cores']} nüvə, {m['hours']} saat")
                                asyncio.create_task(provision_ssh(m["lease"]))
                            elif m["type"] == "lease_closed":
                                print(f"■ İcarə bağlandı {m['lease']} ({m['reason']})")
                                u = ssh_accounts.pop(m["lease"], None)
                                if u: asyncio.create_task(asyncio.get_running_loop().run_in_executor(None, win_delete_temp_user, u))
                            elif m["type"] == "exec":
                                asyncio.create_task(run_cmd(m["lease"], m["cmd_id"], m["cmd"]))
                    await asyncio.gather(hb(), rx())
            except Exception as e:
                print("Əlaqə kəsildi, 5 san sonra yenidən cəhd:", e); await asyncio.sleep(5)
    asyncio.run(loop())

# ───────────────────────── CLI ─────────────────────────
if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(description="IdlePool Desktop App")
    p.set_defaults(cmd="server", host="127.0.0.1", port=8000)
    sub = p.add_subparsers(dest="cmd")

    sv = sub.add_parser("server")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)

    ag = sub.add_parser("agent")
    ag.add_argument("--server", default="ws://localhost:8000/ws/agent")
    ag.add_argument("--key", default="")
    ag.add_argument("--ram-gb", type=positive_float, default=2)
    ag.add_argument("--disk-gb", type=positive_float, default=20)
    ag.add_argument("--cores", type=positive_int, default=1)
    ag.add_argument("--price", type=positive_float, default=5)
    ag.add_argument("--interval", type=positive_int, default=10)
    ag.add_argument("--no-exec", dest="allow_exec", action="store_false", default=True,
                     help="alıcıların bu cihazda uzaq əmr icra etməsinə icazə vermə")
    ag.add_argument("--no-ssh", dest="enable_ssh", action="store_false", default=True,
                     help="alıcılar üçün müvəqqəti SSH girişi yaratma")
    ag.add_argument("--ssh-user", default="", help="sabit SSH istifadəçi adı (boşdursa hər icarə üçün təsadüfi yaradılır)")
    ag.add_argument("--ssh-password", default="", help="sabit SSH şifrəsi (boşdursa hər icarə üçün təsadüfi yaradılır)")

    args = p.parse_args()

    if args.cmd == "agent":
        run_agent(args)
    else:
        run_app(getattr(args, "host", "127.0.0.1"), getattr(args, "port", 8000))