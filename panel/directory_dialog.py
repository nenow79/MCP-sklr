"""Optional Tk directory dialog. Runs only on an explicit panel request."""
import json
import sys


def choose_directory():
    # Lazy imports: headless installations can still start and use the panel.
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=root, title="sklr — Wybierz folder", mustexist=True,
        )
        return selected or None
    finally:
        root.destroy()


if __name__ == "__main__":
    try:
        selected = choose_directory()
    except Exception:
        sys.exit(1)
    # ASCII JSON works even when the interpreter uses a Windows console encoding.
    print(json.dumps({"path": selected}, ensure_ascii=True))
