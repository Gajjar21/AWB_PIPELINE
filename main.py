# main.py
# AWB Pipeline UI Controller
#
# All paths come from config.py / .env.
# No hardcoded paths in this file.

import os
import sys
import json
import glob
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext
from pathlib import Path

import config

# ── Script paths ──────────────────────────────────────────────────────────────
_SCRIPTS = Path(__file__).resolve().parent / "Scripts"
SCRIPT_GET_AWB     = _SCRIPTS / "awb_hotfolder.py"
SCRIPT_EDM_CHECKER = _SCRIPTS / "edm_duplicate_checker.py"
SCRIPT_PRINT_BATCH = _SCRIPTS / "make_print_stack.py"

STATE_FILE = config.BASE_DIR / "_run_state.json"

# ── Protected files (never deleted) ───────────────────────────────────────────
PROTECTED = {p.resolve() for p in config.PROTECTED_FILES}

WORKING_PATTERNS = ["*.pdf", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff",
                    "*.txt", "*.csv", "*.xlsx"]
OUTPUT_FILES_TO_CLEAR = [config.CSV_PATH]

# ── Auto mode ─────────────────────────────────────────────────────────────────
AUTO_INTERVAL_SEC          = config.AUTO_INTERVAL_SEC
AUTO_WAIT_FOR_INBOX_EMPTY  = config.AUTO_WAIT_FOR_INBOX_EMPTY
INBOX_EMPTY_STABLE_SECONDS = config.INBOX_EMPTY_STABLE_SECONDS
INBOX_EMPTY_MAX_WAIT       = config.INBOX_EMPTY_MAX_WAIT
PROCESSED_EMPTY_STABLE_SECONDS = config.PROCESSED_EMPTY_STABLE_SECONDS
PROCESSED_EMPTY_MAX_WAIT       = config.PROCESSED_EMPTY_MAX_WAIT


# =========================
# HELPERS
# =========================
def load_state():
    if not STATE_FILE.exists():
        return {"last_run_id": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_id": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def now_run_id():
    return time.strftime("%Y%m%d_%H%M%S")


def safe_delete_file(fp: Path):
    if fp.resolve() in PROTECTED:
        return False
    if fp.exists():
        try:
            fp.unlink()
            return True
        except Exception:
            return False
    return False


def delete_matching(folder: Path, patterns):
    deleted = 0
    for pat in patterns:
        for fp in folder.glob(pat):
            if fp.resolve() in PROTECTED:
                continue
            try:
                fp.unlink()
                deleted += 1
            except Exception:
                pass
    return deleted


def inbox_pdf_count():
    return len(list(config.INBOX_DIR.glob("*.pdf")))


def clean_pdf_count():
    return len(list(config.CLEAN_DIR.glob("*.pdf")))


def processed_pdf_count():
    return len(list(config.PROCESSED_DIR.glob("*.pdf")))


def wait_until_inbox_empty(log_fn, stable_seconds=8, max_wait=1800):
    start = time.time()
    empty_since = None
    while True:
        n = inbox_pdf_count()
        if n == 0:
            if empty_since is None:
                empty_since = time.time()
                log_fn(f"[AUTO] INBOX empty. Waiting {stable_seconds}s to confirm...")
            if (time.time() - empty_since) >= stable_seconds:
                return True
        else:
            empty_since = None
            log_fn(f"[AUTO] Waiting for INBOX... PDFs remaining: {n}")
        if (time.time() - start) >= max_wait:
            log_fn(f"[AUTO] Timeout after {max_wait}s.")
            return False
        time.sleep(2)


def wait_until_processed_empty(log_fn, stable_seconds=5, max_wait=600):
    start = time.time()
    empty_since = None
    while True:
        n = processed_pdf_count()
        if n == 0:
            if empty_since is None:
                empty_since = time.time()
                log_fn(f"[AUTO] PROCESSED empty. Waiting {stable_seconds}s to confirm...")
            if (time.time() - empty_since) >= stable_seconds:
                return True
        else:
            empty_since = None
            log_fn(f"[AUTO] Waiting for PROCESSED... PDFs remaining: {n}")
        if (time.time() - start) >= max_wait:
            log_fn(f"[AUTO] PROCESSED timeout after {max_wait}s.")
            return False
        time.sleep(2)


# =========================
# UI APP
# =========================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AWB PIPELINE Controller")
        self.geometry("1400x840")
        config.ensure_dirs()

        self.awb_proc        = None
        self.edm_proc        = None
        self.auto_running    = False
        self.auto_stop_event = threading.Event()
        self.auto_thread     = None

        # ── Button row ────────────────────────────────────────────────────────
        btn = tk.Frame(self)
        btn.pack(fill="x", padx=10, pady=8)

        self.btn_get_awb   = tk.Button(btn, text="Start Get AWB",     width=16, command=self.on_toggle_get_awb)
        self.btn_edm       = tk.Button(btn, text="Start EDM Checker", width=18, command=self.on_toggle_edm_checker)
        self.btn_batch     = tk.Button(btn, text="Prepare Batch",     width=14, command=self.on_prepare_batch)
        self.btn_clear_all = tk.Button(btn, text="Clear All",         width=12, command=self.on_clear_all)
        self.btn_auto      = tk.Button(btn, text="Start AUTO MODE",   width=16, command=self.on_toggle_auto_mode)
        self.btn_clear_log = tk.Button(btn, text="Clear Log",         width=10, command=self.clear_log)

        for col, b in enumerate([self.btn_get_awb, self.btn_edm, self.btn_batch,
                                   self.btn_clear_all, self.btn_auto, self.btn_clear_log]):
            b.grid(row=0, column=col, padx=4, pady=5)

        # ── Folder counts bar ─────────────────────────────────────────────────
        counts_frame = tk.Frame(self, bd=1, relief="sunken")
        counts_frame.pack(fill="x", padx=10, pady=(0, 4))

        self.lbl_inbox     = tk.Label(counts_frame, text="INBOX: 0",        width=14, anchor="w")
        self.lbl_processed = tk.Label(counts_frame, text="PROCESSED: 0",    width=16, anchor="w")
        self.lbl_clean     = tk.Label(counts_frame, text="CLEAN: 0",        width=14, anchor="w")
        self.lbl_rejected  = tk.Label(counts_frame, text="REJECTED: 0",     width=16, anchor="w")
        self.lbl_review    = tk.Label(counts_frame, text="NEEDS_REVIEW: 0", width=20, anchor="w")
        self.lbl_out       = tk.Label(counts_frame, text="OUT batches: 0",  width=18, anchor="w")

        for i, lbl in enumerate([self.lbl_inbox, self.lbl_processed, self.lbl_clean,
                                   self.lbl_rejected, self.lbl_review, self.lbl_out]):
            lbl.grid(row=0, column=i, padx=8, pady=2)

        # ── Status ────────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10)

        # ── Log ───────────────────────────────────────────────────────────────
        self.log_widget = scrolledtext.ScrolledText(self, wrap=tk.WORD, height=42)
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_widget.configure(state="disabled")

        self.log_append("PIPELINE  INBOX->[AWB]->PROCESSED->[EDM]->CLEAN/REJECTED->[Batch]->OUT")
        self.log_append(f"BASE DIR: {config.BASE_DIR}")
        self.log_append(f"CLEAN:    {config.CLEAN_DIR}  |  REJECTED: {config.REJECTED_DIR}")
        self.log_append(f"MASTER DB (protected): {config.AWB_EXCEL_PATH}")
        self.log_append(f"AWB Logs (protected):  {config.AWB_LOGS_PATH}")
        self.log_append("Ready.")

        self._start_count_refresh()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── Folder count refresh ──────────────────────────────────────────────────
    def _start_count_refresh(self):
        self._refresh_counts()
        self.after(3000, self._start_count_refresh)

    def _refresh_counts(self):
        def count_pdfs(folder: Path):
            try:
                return len(list(folder.glob("*.pdf")))
            except Exception:
                return "?"

        def count_batches():
            try:
                return len(list(config.OUT_DIR.glob(f"{config.PRINT_STACK_BASENAME}_*.pdf")))
            except Exception:
                return "?"

        self.lbl_inbox.config(    text=f"INBOX: {count_pdfs(config.INBOX_DIR)}")
        self.lbl_processed.config(text=f"PROCESSED: {count_pdfs(config.PROCESSED_DIR)}")
        self.lbl_clean.config(    text=f"CLEAN: {count_pdfs(config.CLEAN_DIR)}")
        self.lbl_rejected.config( text=f"REJECTED: {count_pdfs(config.REJECTED_DIR)}")
        self.lbl_review.config(   text=f"NEEDS_REVIEW: {count_pdfs(config.NEEDS_REVIEW_DIR)}")
        self.lbl_out.config(      text=f"OUT batches: {count_batches()}")

    # ── UI helpers ────────────────────────────────────────────────────────────
    def clear_log(self):
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", tk.END)
        self.log_widget.configure(state="disabled")

    def set_status(self, msg):
        self.status_var.set(msg)
        self.update_idletasks()

    def log_append(self, msg):
        def _do():
            self.log_widget.configure(state="normal")
            self.log_widget.insert(tk.END, msg + "\n")
            self.log_widget.see(tk.END)
            self.log_widget.configure(state="disabled")
        self.after(0, _do)

    def run_in_thread(self, fn):
        def wrapper():
            try:
                fn()
            except Exception as e:
                messagebox.showerror("Error", str(e))
                self.log_append(f"[ERROR] {e}")
                self.set_status("Ready.")
        threading.Thread(target=wrapper, daemon=True).start()

    def _popen_utf8(self, script_path: Path):
        if not script_path.exists():
            raise FileNotFoundError(f"Missing script: {script_path}")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        self.log_append(f"Running: {script_path.name}")
        return subprocess.Popen(
            [sys.executable, "-u", str(script_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, universal_newlines=True, env=env,
        )

    def run_script_blocking_live(self, script_path: Path):
        p = self._popen_utf8(script_path)
        for line in p.stdout:
            self.log_append(line.rstrip("\n"))
        rc = p.wait()
        if rc != 0:
            raise RuntimeError(f"Script failed (exit {rc}). See log above.")

    # ── AWB Hotfolder ─────────────────────────────────────────────────────────
    def is_awb_running(self):
        return self.awb_proc is not None and self.awb_proc.poll() is None

    def start_awb(self):
        if self.is_awb_running():
            return
        save_state({"last_run_id": now_run_id()})
        self.set_status("Get AWB running...")
        self.log_append("\n=== Get AWB started ===")
        self.awb_proc = self._popen_utf8(SCRIPT_GET_AWB)
        self.btn_get_awb.config(text="Stop Get AWB")

        def reader():
            try:
                for line in self.awb_proc.stdout:
                    self.log_append(line.rstrip("\n"))
            except Exception as e:
                self.log_append(f"[AWB ERROR] {e}")
            rc = self.awb_proc.wait()
            self.awb_proc = None
            self.after(0, lambda: self.btn_get_awb.config(text="Start Get AWB"))
            self.set_status("Get AWB stopped." if rc == 0 else "Get AWB ended with errors.")

        threading.Thread(target=reader, daemon=True).start()

    def stop_awb(self):
        if not self.is_awb_running():
            self.awb_proc = None
            self.btn_get_awb.config(text="Start Get AWB")
            return
        self.log_append("Stopping Get AWB...")
        try:
            self.awb_proc.terminate()
            time.sleep(1)
            if self.awb_proc.poll() is None:
                self.awb_proc.kill()
        except Exception:
            pass

    def on_toggle_get_awb(self):
        self.stop_awb() if self.is_awb_running() else self.start_awb()

    # ── EDM Duplicate Checker ─────────────────────────────────────────────────
    def is_edm_running(self):
        return self.edm_proc is not None and self.edm_proc.poll() is None

    def start_edm_checker(self):
        if self.is_edm_running():
            return
        self.log_append("\n=== EDM Checker started ===")
        self.log_append(f"Watching: {config.PROCESSED_DIR}  ->  CLEAN / REJECTED")
        self.edm_proc = self._popen_utf8(SCRIPT_EDM_CHECKER)
        self.btn_edm.config(text="Stop EDM Checker")
        self.set_status("EDM Checker running...")

        def reader():
            try:
                for line in self.edm_proc.stdout:
                    self.log_append(f"[EDM] {line.rstrip()}")
            except Exception as e:
                self.log_append(f"[EDM ERROR] {e}")
            rc = self.edm_proc.wait()
            self.edm_proc = None
            self.after(0, lambda: self.btn_edm.config(text="Start EDM Checker"))
            self.log_append(f"[EDM] Process ended (exit {rc}).")

        threading.Thread(target=reader, daemon=True).start()

    def stop_edm_checker(self):
        if not self.is_edm_running():
            self.edm_proc = None
            self.btn_edm.config(text="Start EDM Checker")
            return
        self.log_append("Stopping EDM Checker...")
        try:
            self.edm_proc.terminate()
            time.sleep(1)
            if self.edm_proc.poll() is None:
                self.edm_proc.kill()
        except Exception:
            pass

    def on_toggle_edm_checker(self):
        self.stop_edm_checker() if self.is_edm_running() else self.start_edm_checker()

    # ── Prepare Batch ─────────────────────────────────────────────────────────
    def on_prepare_batch(self):
        def job():
            n = clean_pdf_count()
            if n == 0:
                self.log_append("[BATCH] CLEAN folder is empty -- nothing to batch.")
                self.set_status("CLEAN is empty.")
                return
            self.set_status(f"Building batch from {n} CLEAN file(s)...")
            self.log_append(f"\n=== Prepare Batch ({n} file(s) in CLEAN) ===")
            self.run_script_blocking_live(SCRIPT_PRINT_BATCH)
            self.log_append("[BATCH] Batch complete. CLEAN sources deleted. Batches saved to OUT.")
            self.set_status("Batch complete.")

        self.run_in_thread(job)

    # ── Clear All ─────────────────────────────────────────────────────────────
    def on_clear_all(self):
        if not messagebox.askyesno(
            "Confirm Clear All",
            "Clears INBOX and OUT working files + run CSV.\n"
            "Does NOT touch: PROCESSED, CLEAN, REJECTED, NEEDS_REVIEW,\n"
            "AWB_dB.xlsx, AWB_Logs.xlsx.\n\nContinue?"
        ):
            return

        def job():
            if self.is_awb_running():
                self.stop_awb(); time.sleep(0.5)
            if self.is_edm_running():
                self.stop_edm_checker(); time.sleep(0.5)

            self.set_status("Clearing...")
            self.log_append("\n=== Clear All ===")
            for fp in OUTPUT_FILES_TO_CLEAR:
                if safe_delete_file(fp):
                    self.log_append(f"Deleted: {fp}")
            self.log_append(f"INBOX cleared:  {delete_matching(config.INBOX_DIR, WORKING_PATTERNS)}")
            self.log_append(f"OUT cleared:    {delete_matching(config.OUT_DIR, WORKING_PATTERNS)}")
            self.log_append("Protected files untouched.")
            save_state({"last_run_id": None})
            self.set_status("Clear complete.")
            if not self.is_awb_running():
                self.start_awb()
            if not self.is_edm_running():
                self.start_edm_checker()

        self.run_in_thread(job)

    # ── Auto Mode ─────────────────────────────────────────────────────────────
    def on_toggle_auto_mode(self):
        self.stop_auto_mode() if self.auto_running else self.start_auto_mode()

    def start_auto_mode(self):
        if self.auto_running:
            return
        self.auto_running = True
        self.auto_stop_event.clear()
        self.btn_auto.config(text="Stop AUTO MODE")
        self.set_status("AUTO MODE running...")
        self.log_append("\n=== AUTO MODE STARTED ===")
        self.log_append("Flow: wait INBOX empty -> wait PROCESSED empty -> Prepare Batch -> repeat")

        if not self.is_awb_running():
            self.start_awb()
        if not self.is_edm_running():
            self.start_edm_checker()

        def loop():
            while not self.auto_stop_event.is_set():
                try:
                    if AUTO_WAIT_FOR_INBOX_EMPTY:
                        ok = wait_until_inbox_empty(
                            self.log_append,
                            INBOX_EMPTY_STABLE_SECONDS,
                            INBOX_EMPTY_MAX_WAIT,
                        )
                        if ok:
                            done = wait_until_processed_empty(
                                self.log_append,
                                PROCESSED_EMPTY_STABLE_SECONDS,
                                PROCESSED_EMPTY_MAX_WAIT,
                            )
                            if done:
                                n = clean_pdf_count()
                                if n == 0:
                                    self.log_append("[AUTO] CLEAN is empty -- nothing to batch yet.")
                                else:
                                    self.log_append(f"[AUTO] Building batch from {n} CLEAN file(s)...")
                                    self.run_script_blocking_live(SCRIPT_PRINT_BATCH)
                                    self.log_append("[AUTO] Batch complete. CLEAN sources deleted.")
                except Exception as e:
                    self.log_append(f"[AUTO ERROR] {e}")

                for _ in range(AUTO_INTERVAL_SEC):
                    if self.auto_stop_event.is_set():
                        break
                    time.sleep(1)

            self.log_append("\n=== AUTO MODE STOPPED ===")
            self.set_status("Ready.")

        self.auto_thread = threading.Thread(target=loop, daemon=True)
        self.auto_thread.start()

    def stop_auto_mode(self):
        if not self.auto_running:
            return
        self.auto_running = False
        self.auto_stop_event.set()
        self.btn_auto.config(text="Start AUTO MODE")
        self.log_append("\nStopping AUTO MODE...")
        self.set_status("Stopping...")

    # ── Close ─────────────────────────────────────────────────────────────────
    def on_close(self):
        if self.auto_running:
            self.stop_auto_mode(); time.sleep(0.5)
        if self.is_awb_running():
            self.stop_awb(); time.sleep(0.5)
        if self.is_edm_running():
            self.stop_edm_checker(); time.sleep(0.5)
        self.destroy()


if __name__ == "__main__":
    config.ensure_dirs()
    App().mainloop()
