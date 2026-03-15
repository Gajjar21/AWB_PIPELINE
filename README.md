# AWB Pipeline

Automated AWB (Air Waybill) document processing pipeline for FedEx shipment documents.

Scans incoming PDFs → matches AWB numbers via OCR → checks against FedEx EDM for duplicates → builds print-ready batch PDFs.

---

## Folder Structure

```
AWB_PIPELINE/
├── config.py                  # Central config - imports from .env
├── main.py                    # UI entry point
├── requirements.txt
├── .env                       # Local config - NOT in git (copy from .env.example)
├── .env.example               # Template - safe to commit
│
├── Scripts/
│   ├── awb_hotfolder.py       # Watches INBOX, OCR matches AWB numbers
│   ├── edm_duplicate_checker.py  # Checks PROCESSED files against FedEx EDM
│   ├── make_print_stack.py    # Builds batch PDFs from CLEAN folder
│   ├── pdf_to_tiff_batch.py   # Converts PDFs to TIFF for printing
│   └── pipeline_tracker.py    # Processing time tracker (Excel)
│
├── pdf_organizer/             # Runtime folders - NOT in git
│   ├── INBOX/                 # Drop PDFs here to process
│   ├── PROCESSED/             # After hotfolder match
│   ├── CLEAN/                 # Passed EDM check - ready to batch
│   ├── REJECTED/              # Duplicate pages found in EDM
│   ├── NEEDS_REVIEW/          # No AWB match found
│   └── PENDING_PRINT/         # TIFF output
│
├── data/                      # Runtime data - NOT in git
│   ├── awb_list.xlsx          # Master AWB reference list
│   ├── AWB_Logs.xlsx          # Match + EDM result log
│   ├── pipeline_tracker.xlsx  # Processing time tracker
│   └── OUT/                   # Batch PDFs + sequence Excel
│
├── logs/                      # Runtime logs - NOT in git
│   ├── pipeline.log
│   └── edm_checker.log
│
└── Manual_Libraries/          # Local lib installs if needed - NOT in git
```

---

## Setup

### Prerequisites

**Both Mac and Windows:**
- Python 3.11+
- Tesseract OCR

**Mac (install via Homebrew):**
```bash
brew install tesseract
```

**Windows:**
Download and install Tesseract from:
https://github.com/UB-Mannheim/tesseract/wiki

Default Windows install path: `C:\Program Files\Tesseract-OCR\tesseract.exe`

---

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/awb-pipeline.git
cd awb-pipeline
```

### 2. Create a virtual environment

**Mac:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows:**
```cmd
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your .env

```bash
cp .env.example .env
```

Then edit `.env` with your local values:

| Variable | Mac example | Windows example |
|---|---|---|
| `PIPELINE_BASE_DIR` | `/Users/yourname/Desktop/AWB_PIPELINE` | `C:\Users\5834089\Downloads\AWB_PIPELINE` |
| `TESSERACT_PATH` | `/usr/local/bin/tesseract` | `C:\Users\5834089\Downloads\CCD_Filler\tesseract.exe` |
| `EDM_TOKEN` | *(paste from FedEx portal)* | *(paste from FedEx portal)* |

### 5. Verify config

```bash
python config.py
```

Should print all paths and `All checks passed.`

### 6. Run the pipeline UI

```bash
python main.py
```

---

## Workflow

```
INBOX → [awb_hotfolder] → PROCESSED → [edm_duplicate_checker] → CLEAN / REJECTED
                                                                      ↓
                                                              [make_print_stack]
                                                                      ↓
                                                                  OUT/PRINT_STACK_BATCH_*.pdf
```

1. Drop PDFs into `pdf_organizer/INBOX/`
2. Start **Get AWB** in the UI — hotfolder matches AWB numbers via filename, text layer, or OCR
3. Start **EDM Checker** — compares each file against FedEx EDM to detect duplicates
4. Click **Prepare Batch** — builds numbered batch PDFs with barcode cover pages into `data/OUT/`

---

## EDM Token

The FedEx EDM token expires periodically. When it expires:
- The EDM Checker process will stop with exit code 1
- Update `EDM_TOKEN` in your `.env` file
- Restart the EDM Checker from the UI

**Never commit your token to git.** `.env` is in `.gitignore`.

---

## Development Notes

- Develop on **Mac**, deploy/run on **Windows**
- All paths use `pathlib.Path` — cross-platform safe
- `.env` holds all machine-specific config — no hardcoded paths in any script
- Run `python config.py` on any new machine to verify the setup before starting

---

## Dependencies

See `requirements.txt`. Key libraries:
- **PyMuPDF** — PDF reading and manipulation
- **pytesseract** — OCR wrapper for Tesseract
- **rapidfuzz** — fuzzy text matching for EDM duplicate detection
- **watchdog** — file system event watching
- **reportlab** — barcode cover page generation
- **python-dotenv** — `.env` file loading
