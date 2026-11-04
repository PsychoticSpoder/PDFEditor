# PDFEditor — Roadmap & Improvement Recommendations

> **Implementation status:** Items 1–6 and 8 are **implemented** (✅). Items 7 and 9 are
> **partially implemented** — remaining sub-tasks are left open as `[ ]` with notes.
> All 31 automated tests pass (`pytest -q`).

## Summary of current state (updated)

The project is now an installable `pdfeditor` package:

- **`pdfeditor/core/`** — `constants.py`, `io_utils.py`, `ranges.py`, `pdf_ops.py`, `office.py`
- **`pdfeditor/cli.py`** — CLI (`pdf-tool` console script / `python -m pdfeditor`)
- **`pdfeditor/gui.py`** — PySide6 GUI (`pdf-editor-gui` console script)
- **`pdf_tool.py`, `pdf_gui.py`** — thin backward-compatibility shims
- **`tests/`** + **`.github/workflows/ci.yml`** — pytest suite and CI
- **`pyproject.toml`**, **`.gitignore`**

## Priority overview

| # | Area                                    | Impact | Effort | Status      |
|---|-----------------------------------------|--------|--------|-------------|
| 1 | Add a test suite                        | High   | Medium | ✅ Done      |
| 2 | Package & restructure the project       | High   | Medium | ✅ Done      |
| 3 | Repo hygiene (.gitignore, untrack pyc)  | High   | Low    | ✅ Done      |
| 4 | De-duplicate CLI ↔ GUI validation       | Medium | Low    | ✅ Done      |
| 5 | Office/COM robustness                   | High   | Medium | ✅ Done      |
| 6 | Move GUI heavy work off main thread     | High   | Medium | ✅ Done      |
| 7 | Behavior / UX improvements              | Medium | Medium | ◐ Partial   |
| 8 | Docs & security accuracy                | Medium | Low    | ✅ Done      |
| 9 | Lower priority / nitpicks               | Low    | Low    | ◐ Partial   |

---

## 1. Add a test suite ✅ (done)

- [x] Add a `tests/` package (`conftest.py`, `test_ranges.py`, `test_io_utils.py`,
      `test_pdf_ops.py`, `test_office.py`).
- [x] Add a `pyproject.toml` with `[tool.pytest.ini_options]` and `pytest-cov`.
- [x] Add a GitHub Actions workflow (`.github/workflows/ci.yml`).
- [x] Exercise Office logic without COM via fakes (`test_office.py`).

## 2. Package and restructure the project ✅ (done)

- [x] Add **`pyproject.toml`** with console-script entry points (`pdf-tool`, `pdf-editor-gui`).
- [x] Enable `pip install -e .`.
- [x] Remove the import-time `logging.basicConfig`; configure logging in `main()`
      (`pdfeditor.cli.configure_logging`, GUI `main()`).
- [x] Split the monolith into `pdfeditor/core/{constants,io_utils,ranges,pdf_ops,office}.py`,
      `pdfeditor/cli.py`, `pdfeditor/gui.py`.
- [x] Keep `pdf_tool.py` / `pdf_gui.py` as thin shims so old invocations still work.

## 3. Repo hygiene ✅ (done)

- [x] Add a `.gitignore` (`__pycache__/`, `*.py[cod]`, `.venv/`, `dist/`, `build/`,
      `*.egg-info/`, `.pytest_cache/`, ...).
- [x] `git rm -r --cached __pycache__`.

## 4. De-duplicate CLI ↔ GUI validation ✅ (done)

- [x] GUI path helpers now delegate to the core validators (`_validate_existing_file`,
      `_validate_output_path`, `_validate_output_dir`, `_validate_existing_dir`).
- [x] "Actions that need an output dir" and "Office actions" are defined once in
      `pdfeditor/core/constants.py` (`OUTPUT_DIR_ACTIONS`, `OFFICE_ACTIONS`) and reused by the GUI.

## 5. Office/COM robustness ✅ (done)

- [x] Extract a `_com_application()` context manager; Word/Excel (single and batch) share it.
- [x] Excel per-sheet temp PDFs now go to `tempfile.TemporaryDirectory()` (never the output folder).
- [x] Remove the redundant `from pypdf import PdfWriter, PdfReader` inside `_combine_pdfs_in_order`.
- [x] Blank-page removal is opt-in (`skip_blank_pages=False` default) and every skipped page is
      logged; only the Excel combiner opts in.
- [x] `_configure_excel_worksheet()` centralizes the repeated page-setup logic.
- [ ] Watchdog/timeout to kill a hung Word/Excel process — **not implemented** (follow-up).

## 6. GUI runs heavy work on the main thread ✅ (done)

- [x] Single-file ops (rotate/watermark/combine/delete/reorder/split/images/text/print) run on a
      background `QThread` via `SingleOpWorker` + `MainWindow._run_async`.
- [x] Watermark opacity uses a validated `QDoubleSpinBox` (0.0–1.0) instead of free-text parsing.
- [x] Removed the duplicated hard-coded action lists (reuse `OUTPUT_DIR_ACTIONS` / `OFFICE_ACTIONS`).

## 7. Behavior / UX improvements ◐ (partial)

- [x] Atomic writes: outputs are written to a temp file then `os.replace`d into place
      (`_atomic_write_bytes` / `_atomic_write_text` / `_atomic_write_pdf`).
- [x] `images_to_pdf` streams one image at a time (no longer loads all images into memory) and
      sizes each page to the image aspect ratio.
- [x] `print_pdf` documented as best-effort/"submitted" (it does not confirm printing).
- [x] `--verbose` / `--quiet` global flags and the `PDF_TOOL_LOG_LEVEL` environment variable.
- [ ] `--overwrite` / `--skip-existing` and `--dry-run` flags — **not implemented** (follow-up).
- [ ] Cross-platform LibreOffice (`soffice --headless`) fallback for Office conversion — **not implemented**.

## 8. Docs & security accuracy ✅ (done)

- [x] README "Security Notes" corrected (removed the misleading traversal-attack claim; documents
      atomic writes and temp-file handling).
- [x] README logging section now uses `--verbose` / `PDF_TOOL_LOG_LEVEL` (no source edits).
- [x] README documents `pip install -e .` and the `pdf-tool` / `pdf-editor-gui` commands.
- [x] `analyze_excel.py` no longer hardcodes a personal path — it takes the file path as an argument.

## 9. Lower priority / nitpicks ◐ (partial)

- [x] Tool config for `black` / `ruff` / `mypy` added to `pyproject.toml`.
- [x] Documented the `MAX_WORKERS` vs `_resolve_workers` auto-cap distinction (constants comment).
- [x] Office swallowed exceptions now log at `debug` where practical.
- [ ] `pre-commit` hook — **not implemented** (follow-up).
- [ ] New features (PDF→image, page insert/extract, redaction, metadata editing) — **not implemented**.

---

## Suggested order of work

1. [x] `.gitignore` + untrack `__pycache__`.
2. [x] `pyproject.toml` + `tests/` (pure helpers + integration tests).
3. [x] Package restructure with CLI/GUI entry points; removed import-time logging config.
4. [x] De-duplicated validation; moved GUI single-file operations onto a worker thread.
5. [x] Hardened Office/COM (context manager, temp dirs). Watchdog/timeout remains a follow-up.

### Open follow-ups

- Office watchdog/timeout.
- `--overwrite` / `--skip-existing` / `--dry-run` CLI flags.
- LibreOffice fallback for non-Windows Office conversion.
- `pre-commit` hook and additional feature work.

