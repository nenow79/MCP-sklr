#!/usr/bin/env python3
"""TYMCZASOWY dialog potwierdzeń (faza 3). W fazie 4 zastąpiony przez ApprovalService + stałe okno GUI."""
import threading
import tkinter as tk

_DIALOG_LOCK = threading.Lock()  # jedno okno naraz


def ask_skill_confirmation(title: str, message: str, allow_trust: bool, timeout_s: int) -> str:
    """Zwraca: 'allow' | 'trust' | 'deny' | 'timeout'."""
    with _DIALOG_LOCK:
        result = {"value": "deny"}
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            print(f"[GUI] Nie można otworzyć okna potwierdzenia: {exc}")
            return "deny"

        root.title(title)
        root.geometry("720x520")
        root.minsize(520, 360)
        root.attributes("-topmost", True)
        root.configure(bg="#1e1e1e")

        tk.Label(
            root, text=f"⚠️  {title}", font=("Arial", 13, "bold"), fg="#e8a317", bg="#1e1e1e"
        ).pack(pady=(14, 6), padx=20)

        btn_frame = tk.Frame(root, bg="#1e1e1e")
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(4, 14))

        tk.Label(
            root, text=f"Brak decyzji przez {timeout_s} s = odrzucenie.",
            font=("Arial", 9), fg="#858585", bg="#1e1e1e",
        ).pack(side=tk.BOTTOM, pady=(0, 2))

        msg_frame = tk.Frame(root, bg="#1e1e1e")
        msg_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))
        scroll_y = tk.Scrollbar(msg_frame)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        text = tk.Text(
            msg_frame, wrap=tk.WORD, yscrollcommand=scroll_y.set,
            font=("Consolas", 10), fg="#d4d4d4", bg="#2d2d2d",
            relief=tk.FLAT, bd=0, padx=10, pady=8,
        )
        text.insert(tk.END, message)
        text.config(state=tk.DISABLED)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.config(command=text.yview)

        def finish(value: str) -> None:
            result["value"] = value
            root.destroy()

        tk.Button(
            btn_frame, text="✅  Uruchom", command=lambda: finish("allow"),
            bg="#2ea043", fg="white", font=("Arial", 10, "bold"), width=16, height=2, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))
        if allow_trust:
            tk.Button(
                btn_frame, text="🔒  Ufaj i uruchom", command=lambda: finish("trust"),
                bg="#1f6feb", fg="white", font=("Arial", 10, "bold"), width=18, height=2, cursor="hand2",
            ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            btn_frame, text="❌  Zablokuj", command=lambda: finish("deny"),
            bg="#da3633", fg="white", font=("Arial", 10, "bold"), width=16, height=2, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 6))

        root.protocol("WM_DELETE_WINDOW", lambda: finish("deny"))
        root.after(max(1, int(timeout_s)) * 1000, lambda: finish("timeout"))
        root.lift()
        root.focus_force()
        root.bell()
        root.wait_window()
        return result["value"]
