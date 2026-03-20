# AWB Pipeline

Automated AWB (Air Waybill) document processing pipeline for FedEx shipment documents.

Scans incoming PDFs → matches AWB numbers via OCR → checks against FedEx EDM for duplicates → builds print-ready batch PDFs.

---

## Repository Overview
This repository contains tools and automation scripts for managing AWB (Air Waybill) documents essential for FedEx shipment workflows. The pipeline efficiently handles:
- Scanning incoming shipment PDFs.
- Optimized AWB matching through filename detection, text layers, or OCR.
- Checking shipments against centralized EDM (Electronic Data Management) systems to avoid duplicates.
- Creating batch-ready PDF documents.

## Project Documents
- Senior management overview: [docs/Senior_Management_Overview.md](docs/Senior_Management_Overview.md)
- Technical deep dive: [docs/Technical_Deep_Dive.md](docs/Technical_Deep_Dive.md)

---

## Folder Structure

```
.
├── config.py                     # Central configuration file.
├── main.py                       # Entry point with GUI elements.
├── requirements.txt              # Python dependencies.
├── .env                          # Local environment configurations (excluded from version control).
├── Scripts                       # Core functionality scripts.
│   ├── awb_hotfolder_V2.py       # Main script watching INBOX and managing OCR.
│   ├── edm_duplicate_checker.py  # Integrates FedEx EDM duplicate check API.
│   ├── make_print_stack.py       # Creates stacked PDFs for printing.
│   ├── audit_logger.py           # Logs system activity with rotating backup.
│   ├── centralized_audit.py      # Unified auditing and Excel report creator.
│   ├── pipeline_healthcheck.py   # Ensures readiness of all dependencies and components.
├── data                          # Input, output, and runtime data.
    ├── AWB_dB.xlsx               # Master database of AWB records.
    ├── AWB_Logs.xlsx             # Logs of each processed AWB.
    ├── pipeline_tracker.xlsx     # Tracking durations and events.
    ├── edm_awb_exists_cache.json # Caches API calls for optimized routing.
    ├── OUT/                      # Batch-ready documents.
├── LOGS                          # Logs folder excluded from git to store real-time logs.
│   ├── edm_checker.log
│   ├── pipeline_audit.jsonl
```

---

## Setup Guide

### Prerequisites
The project requires both Python 3.11+, Tesseract OCR, and specific environment configs in a `.env` file. Configurations should include:

| Variable              | Mac Example                       | Windows Example                    |
|-----------------------|-----------------------------------|------------------------------------|
| PIPELINE_BASE_DIR     | `/Users/yours/AWB_PIPELINE`       | `C:\Users\yours\AWB_PIPELINE`  |
| TESSERACT_PATH        | `/usr/local/bin/tesseract`        | `C:\Programs\tesseract.exe`     |
| EDM_TOKEN             | *(FedEx Portals)*                | *(Paste Secure values).*           |

### Full Steps:
- Clone the Repo:
```
git clone https://github.com/Gajjar21/AWB_PIPELINE 
```
- Setup venv/env.

