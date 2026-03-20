"""
Pipeline healthcheck utility.

Usage:
  python Scripts/pipeline_healthcheck.py
"""

import importlib
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


class Healthcheck:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.passes = []

    def ok(self, msg):
        self.passes.append(msg)
        print(f"[PASS] {msg}")

    def warn(self, msg):
        self.warnings.append(msg)
        print(f"[WARN] {msg}")

    def err(self, msg):
        self.errors.append(msg)
        print(f"[FAIL] {msg}")


def _check_exists(hc, label, path, required=True):
    if Path(path).exists():
        hc.ok(f"{label}: {path}")
    elif required:
        hc.err(f"{label} missing: {path}")
    else:
        hc.warn(f"{label} not found: {path}")


def _check_writable(hc, folder: Path):
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".healthcheck_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        hc.ok(f"Writable: {folder}")
    except Exception as exc:
        hc.err(f"Not writable: {folder} ({exc})")


def _check_import(hc, module_name, required=True):
    try:
        importlib.import_module(module_name)
        hc.ok(f"Dependency import: {module_name}")
    except Exception as exc:
        if required:
            hc.err(f"Dependency missing/broken: {module_name} ({exc})")
        else:
            hc.warn(f"Optional dependency unavailable: {module_name} ({exc})")


def run():
    hc = Healthcheck()
    print("=== Pipeline Healthcheck ===")
    print(f"Base dir: {config.BASE_DIR}")
    print()

    # Ensure runtime folders exist
    try:
        config.ensure_dirs()
        hc.ok("config.ensure_dirs() completed")
    except Exception as exc:
        hc.err(f"config.ensure_dirs() failed: {exc}")

    # Core paths
    _check_exists(hc, "Tesseract", config.TESSERACT_PATH, required=True)
    _check_exists(hc, "AWB DB", config.AWB_EXCEL_PATH, required=True)
    _check_exists(hc, "AWB Logs", config.AWB_LOGS_PATH, required=False)

    # Script entrypoints
    scripts_dir = Path(__file__).resolve().parent
    required_scripts = [
        scripts_dir / "awb_hotfolder_V2.py",
        scripts_dir / "edm_duplicate_checker.py",
        scripts_dir / "make_print_stack.py",
        scripts_dir / "pdf_to_tiff_batch.py",
        scripts_dir / "pipeline_tracker.py",
        scripts_dir / "pipeline_tracker_locksafe.py",
        scripts_dir / "centralized_audit.py",
    ]
    for script in required_scripts:
        _check_exists(hc, "Script", script, required=True)

    # Write permissions
    for folder in (config.LOG_DIR, config.DATA_DIR, config.OUT_DIR, config.PENDING_PRINT_DIR):
        _check_writable(hc, folder)

    # Token readiness (warning only, not fatal)
    token = (config.EDM_TOKEN or "").strip()
    token_file_ok = config.TOKEN_FILE.exists()
    if token and token.lower() != "paste_your_token_here":
        hc.ok("EDM token present via environment")
    elif token_file_ok:
        hc.ok(f"EDM token file present: {config.TOKEN_FILE}")
    else:
        hc.warn("No EDM token configured; EDM duplicate check fallback API calls may be skipped")

    # Dependency checks
    required_deps = [
        "openpyxl",
        "requests",
        "PIL",
        "pytesseract",
        "watchdog",
        "rapidfuzz",
        "reportlab",
        "numpy",
    ]
    for dep in required_deps:
        _check_import(hc, dep, required=True)

    # PyMuPDF can be imported as fitz or pymupdf depending on environment
    try:
        importlib.import_module("fitz")
        hc.ok("Dependency import: fitz")
    except Exception:
        _check_import(hc, "pymupdf", required=True)

    # Optional modules used conditionally
    _check_import(hc, "cv2", required=False)
    _check_import(hc, "imagehash", required=False)

    print()
    print("=== Summary ===")
    print(f"PASS: {len(hc.passes)}")
    print(f"WARN: {len(hc.warnings)}")
    print(f"FAIL: {len(hc.errors)}")

    if hc.errors:
        print("Healthcheck status: FAILED")
        return 1
    print("Healthcheck status: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

