#!/usr/bin/env python3
"""BVOD Processor — Desktop App (CustomTkinter)"""

import os
import sys
import queue
import platform
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import tkinter as tk

# Optional drag-and-drop support
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES

    class _AppRoot(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self):
            super().__init__()
            self.TkdndVersion = TkinterDnD._require(self)

    HAS_DND = True
except ImportError:
    _AppRoot = ctk.CTk
    HAS_DND = False

# Import core processing module from same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import process_video as pv
from process_video import load_config, SUPPORTED_EXTENSIONS


# ── Helpers ───────────────────────────────────────────────────────────

def _parse_dnd_paths(raw: str) -> list:
    """Parse path string from TkinterDnD drop event.
    Paths with spaces are wrapped in curly braces: {/path/with spaces}
    """
    paths = []
    raw = raw.strip()
    i = 0
    while i < len(raw):
        if raw[i] == '{':
            end = raw.index('}', i)
            paths.append(raw[i + 1:end])
            i = end + 2
        else:
            end = raw.find(' ', i)
            if end == -1:
                paths.append(raw[i:])
                break
            paths.append(raw[i:end])
            i = end + 1
    return [p for p in paths if p]


# ── App ───────────────────────────────────────────────────────────────

class BVODApp:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = _AppRoot()
        self.root.title("BVOD Processor")
        self.root.geometry("580x760")
        self.root.minsize(480, 580)

        # Load config defaults
        try:
            titlecard_path, output_suffix = load_config()
        except Exception:
            titlecard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "titlecard.png")
            output_suffix = "_final"

        # State
        self.queued_files: list = []
        self.log_queue: queue.Queue = queue.Queue()
        self.processing = False

        # Config vars
        self.titlecard_var        = tk.StringVar(value=titlecard_path)
        self.suffix_var           = tk.StringVar(value=output_suffix)
        self.titlecard_secs_var   = tk.DoubleVar(value=1.5)
        self.audio_fade_var       = tk.DoubleVar(value=2.0)
        self.short_total_var      = tk.DoubleVar(value=15.0)
        self.long_total_var       = tk.DoubleVar(value=30.0)
        self.settings_open        = tk.BooleanVar(value=False)

        self._build_ui()
        self._poll_log_queue()

    # ── UI Construction ───────────────────────────────────────────────

    def _build_ui(self):
        root = self.root

        main = ctk.CTkFrame(root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=(16, 12))

        # Header
        ctk.CTkLabel(
            main, text="BVOD Processor",
            font=ctk.CTkFont(size=22, weight="bold"),
            anchor="w"
        ).pack(fill="x")
        ctk.CTkLabel(
            main, text="Append a title card and trim/pad to 15 s or 30 s",
            text_color="gray60", font=ctk.CTkFont(size=12), anchor="w"
        ).pack(fill="x", pady=(0, 14))

        # Drop zone
        self._build_dropzone(main)

        # File list
        self._build_file_list(main)

        # Settings
        self._build_settings_toggle(main)
        self.settings_frame = self._make_settings_frame(main)
        # (not packed yet — collapsed by default)

        # Process button
        self.process_btn = ctk.CTkButton(
            main,
            text="Process Files",
            height=46,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_process,
        )
        self.process_btn.pack(fill="x", pady=(10, 0))

        # Progress bar (hidden until processing)
        self.progress_bar = ctk.CTkProgressBar(main, mode="indeterminate", height=6)
        # not packed initially

        # Log
        self._build_log(main)

    def _build_dropzone(self, parent):
        zone = ctk.CTkFrame(
            parent, height=110, corner_radius=12,
            border_width=2, border_color="#3a3a5c"
        )
        zone.pack(fill="x", pady=(0, 10))
        zone.pack_propagate(False)

        inner = ctk.CTkFrame(zone, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        if HAS_DND:
            icon_text = "⬇  Drop videos here"
        else:
            icon_text = "Click to select videos"

        ctk.CTkLabel(
            inner, text=icon_text,
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack()

        if HAS_DND:
            ctk.CTkLabel(inner, text="or", text_color="gray60",
                         font=ctk.CTkFont(size=11)).pack()
            ctk.CTkButton(
                inner, text="Browse files", width=120, height=28,
                command=self._browse_files
            ).pack(pady=(4, 0))
            zone.drop_target_register(DND_FILES)
            zone.dnd_bind("<<Drop>>", self._on_drop)
        else:
            ctk.CTkLabel(
                inner, text="(install tkinterdnd2 for drag & drop)",
                text_color="#555566", font=ctk.CTkFont(size=10)
            ).pack(pady=(2, 0))
            zone.bind("<Button-1>", lambda _: self._browse_files())
            inner.bind("<Button-1>", lambda _: self._browse_files())

        self.dropzone = zone

    def _build_file_list(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            row, text="Queued files",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left")
        ctk.CTkButton(
            row, text="Clear all", width=70, height=24,
            fg_color="transparent", border_width=1,
            command=self._clear_files
        ).pack(side="right")

        self.file_list_frame = ctk.CTkScrollableFrame(
            parent, height=100, fg_color="#111122", corner_radius=8
        )
        self.file_list_frame.pack(fill="x", pady=(0, 8))
        self._refresh_file_list()

    def _build_settings_toggle(self, parent):
        self.settings_toggle_btn = ctk.CTkButton(
            parent,
            text="▶  Settings",
            anchor="w",
            fg_color="transparent",
            text_color="gray60",
            hover=False,
            height=26,
            font=ctk.CTkFont(size=13),
            command=self._toggle_settings,
        )
        self.settings_toggle_btn.pack(fill="x", pady=(0, 4))

    def _make_settings_frame(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="#111122", corner_radius=10)
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        # Titlecard path
        self._build_path_row(inner, "Titlecard PNG", self.titlecard_var, self._browse_titlecard)

        # Output suffix
        r = ctk.CTkFrame(inner, fg_color="transparent")
        r.pack(fill="x", pady=3)
        ctk.CTkLabel(r, text="Output suffix", width=130, anchor="w").pack(side="left")
        ctk.CTkEntry(r, textvariable=self.suffix_var, height=28, width=100).pack(side="left", padx=(8, 0))

        # Sliders
        self._build_slider(inner, "Title card",  self.titlecard_secs_var, 0.5, 5.0, 45, "s")
        self._build_slider(inner, "Audio fade",  self.audio_fade_var,     0.5, 5.0, 45, "s")
        self._build_slider(inner, "15 s target", self.short_total_var,    8.0, 30.0, 22, "s")
        self._build_slider(inner, "30 s target", self.long_total_var,     15.0, 60.0, 45, "s")

        ctk.CTkButton(
            inner, text="Reset to defaults", width=150, height=28,
            fg_color="transparent", border_width=1,
            command=self._reset_settings
        ).pack(anchor="e", pady=(10, 0))

        return frame

    def _build_path_row(self, parent, label, var, browse_cmd):
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill="x", pady=3)
        ctk.CTkLabel(r, text=label, width=130, anchor="w").pack(side="left")
        ctk.CTkEntry(r, textvariable=var, height=28).pack(
            side="left", fill="x", expand=True, padx=(8, 4)
        )
        ctk.CTkButton(r, text="Browse", width=70, height=28, command=browse_cmd).pack(side="left")

    def _build_slider(self, parent, label, var, from_, to_, steps, unit):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text=label, width=130, anchor="w").pack(side="left")
        val_lbl = ctk.CTkLabel(row, text=f"{var.get():.1f}{unit}", width=50, anchor="e")
        val_lbl.pack(side="right")
        slider = ctk.CTkSlider(
            row, variable=var, from_=from_, to=to_,
            number_of_steps=steps
        )
        slider.pack(side="left", fill="x", expand=True, padx=(8, 8))

        def _update(_val, lbl=val_lbl, v=var, u=unit):
            lbl.configure(text=f"{v.get():.1f}{u}")

        slider.configure(command=_update)

    def _build_log(self, parent):
        ctk.CTkLabel(
            parent, text="Output log",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w"
        ).pack(fill="x", pady=(10, 4))

        self.log_text = ctk.CTkTextbox(
            parent, height=180,
            font=ctk.CTkFont(family="Courier", size=11)
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    # ── Event handlers ────────────────────────────────────────────────

    def _on_drop(self, event):
        self._add_files(_parse_dnd_paths(event.data))

    def _browse_files(self):
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="Select video files",
            filetypes=[("Video files", exts), ("All files", "*.*")]
        )
        self._add_files(list(paths))

    def _browse_titlecard(self):
        path = filedialog.askopenfilename(
            title="Select titlecard PNG",
            filetypes=[("PNG files", "*.png"), ("All files", "*.*")]
        )
        if path:
            self.titlecard_var.set(path)

    def _add_files(self, paths: list):
        added = 0
        for path in paths:
            path = path.strip()
            if not path:
                continue
            ext = Path(path).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                self._log(f"⚠  Skipped: {Path(path).name} — unsupported format ({ext})")
                continue
            if path not in self.queued_files:
                self.queued_files.append(path)
                added += 1
        if added:
            self._refresh_file_list()
            self._update_process_btn()

    def _refresh_file_list(self):
        for w in self.file_list_frame.winfo_children():
            w.destroy()

        if not self.queued_files:
            ctk.CTkLabel(
                self.file_list_frame,
                text="No files added yet",
                text_color="#555577",
                font=ctk.CTkFont(size=12)
            ).pack(pady=8)
            return

        for i, path in enumerate(self.queued_files):
            row = ctk.CTkFrame(self.file_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(
                row, text=Path(path).name,
                anchor="w", font=ctk.CTkFont(size=12)
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row, text="✕", width=26, height=22,
                fg_color="transparent",
                command=lambda idx=i: self._remove_file(idx)
            ).pack(side="right")

    def _remove_file(self, idx: int):
        if idx < len(self.queued_files):
            self.queued_files.pop(idx)
            self._refresh_file_list()
            self._update_process_btn()

    def _clear_files(self):
        self.queued_files.clear()
        self._refresh_file_list()
        self._update_process_btn()

    def _toggle_settings(self):
        if self.settings_open.get():
            self.settings_frame.pack_forget()
            self.settings_open.set(False)
            self.settings_toggle_btn.configure(text="▶  Settings")
        else:
            self.settings_frame.pack(fill="x", pady=(0, 8),
                                     before=self.process_btn)
            self.settings_open.set(True)
            self.settings_toggle_btn.configure(text="▼  Settings")

    def _reset_settings(self):
        self.titlecard_secs_var.set(1.5)
        self.audio_fade_var.set(2.0)
        self.short_total_var.set(15.0)
        self.long_total_var.set(30.0)
        self.suffix_var.set("_final")

    def _update_process_btn(self):
        n = len(self.queued_files)
        if self.processing or n == 0:
            self.process_btn.configure(state="disabled",
                                       text="Processing..." if self.processing else "Process Files")
        else:
            label = f"Process {n} file{'s' if n != 1 else ''}"
            self.process_btn.configure(text=label, state="normal")

    def _on_process(self):
        if not self.queued_files or self.processing:
            return

        titlecard = self.titlecard_var.get().strip()
        if not os.path.isfile(titlecard):
            from tkinter import messagebox
            messagebox.showerror(
                "Titlecard not found",
                f"Could not find the titlecard PNG at:\n{titlecard}\n\n"
                "Update the path in Settings."
            )
            return

        self.processing = True
        self._update_process_btn()
        self.progress_bar.pack(fill="x", pady=(6, 0), before=self.log_text)
        self.progress_bar.start()

        files = list(self.queued_files)
        overrides = {
            "title_card_seconds":  self.titlecard_secs_var.get(),
            "audio_fade_seconds":  self.audio_fade_var.get(),
            "short_total_seconds": self.short_total_var.get(),
            "long_total_seconds":  self.long_total_var.get(),
        }
        suffix = self.suffix_var.get().strip() or "_final"

        threading.Thread(
            target=self._process_worker,
            args=(files, titlecard, suffix, overrides),
            daemon=True
        ).start()

    # ── Background worker ─────────────────────────────────────────────

    def _process_worker(self, files, titlecard, suffix, overrides):
        successes, failures = [], []

        tc_secs    = overrides["title_card_seconds"]
        tc_frames  = round(tc_secs * pv.OUTPUT_FPS)
        short_tot  = overrides["short_total_seconds"]
        long_tot   = overrides["long_total_seconds"]
        fade_secs  = overrides["audio_fade_seconds"]

        # Snapshot originals so we can restore after
        saved = {
            "TITLE_CARD_FRAMES":    pv.TITLE_CARD_FRAMES,
            "TITLE_CARD_DURATION":  pv.TITLE_CARD_DURATION,
            "CONTENT_SHORT_FRAMES": pv.CONTENT_SHORT_FRAMES,
            "CONTENT_LONG_FRAMES":  pv.CONTENT_LONG_FRAMES,
            "CONTENT_SHORT_SECONDS":pv.CONTENT_SHORT_SECONDS,
            "CONTENT_LONG_SECONDS": pv.CONTENT_LONG_SECONDS,
            "AUDIO_FADE_DURATION":  pv.AUDIO_FADE_DURATION,
        }

        try:
            pv.TITLE_CARD_FRAMES     = tc_frames
            pv.TITLE_CARD_DURATION   = tc_secs
            pv.CONTENT_SHORT_SECONDS = short_tot - tc_secs
            pv.CONTENT_LONG_SECONDS  = long_tot  - tc_secs
            pv.CONTENT_SHORT_FRAMES  = round(pv.CONTENT_SHORT_SECONDS * pv.OUTPUT_FPS)
            pv.CONTENT_LONG_FRAMES   = round(pv.CONTENT_LONG_SECONDS  * pv.OUTPUT_FPS)
            pv.AUDIO_FADE_DURATION   = fade_secs

            for path in files:
                name = Path(path).name
                self._log(f"⏳  {name}")
                output_path, error = pv.process_file(path, titlecard, suffix)
                if error:
                    self._log(f"❌  {name} — {error}")
                    failures.append(name)
                else:
                    out_name = Path(output_path).name
                    self._log(f"✅  {out_name}")
                    successes.append(output_path)

        finally:
            for k, v in saved.items():
                setattr(pv, k, v)

        self._log(f"\n{'─' * 44}")
        total = len(successes) + len(failures)
        self._log(f"Done — {len(successes)}/{total} succeeded.")
        if failures:
            for f in failures:
                self._log(f"   • failed: {f}")

        # Open output folder on completion
        if successes:
            folder = str(Path(successes[-1]).parent)
            try:
                if platform.system() == "Windows":
                    subprocess.Popen(["explorer", folder])
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", folder])
            except Exception:
                pass

        self.log_queue.put(("DONE", None))

    # ── Log polling ───────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_queue.put(("LOG", msg))

    def _poll_log_queue(self):
        try:
            while True:
                event, data = self.log_queue.get_nowait()
                if event == "LOG":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", data + "\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif event == "DONE":
                    self.processing = False
                    self.progress_bar.stop()
                    self.progress_bar.pack_forget()
                    self._update_process_btn()
        except queue.Empty:
            pass
        self.root.after(80, self._poll_log_queue)

    def run(self):
        self._update_process_btn()
        self.root.mainloop()


if __name__ == "__main__":
    app = BVODApp()
    app.run()
