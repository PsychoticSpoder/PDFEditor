# PDFEditor — Roadmap & Improvement Recommendations

> **Implementation status:** Items 1–8 are **implemented** (✅). Item 9 is **partially
> implemented** — page extract/insert and metadata editing shipped; PDF→image and
> redaction remain open `[ ]` (deferred). All automated tests pass (`pytest -q`).
> A GUI usability plan is captured in [Section 10](#10-gui-usability--intuitiveness-planned).

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
| 7 | Behavior / UX improvements              | Medium | Medium | ✅ Done      |
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
- [x] Watchdog/timeout to kill a hung Word/Excel process (`Deadline` +
      `OFFICE_OPERATION_TIMEOUT_SECONDS`, force-kill via `taskkill`).

## 6. GUI runs heavy work on the main thread ✅ (done)

- [x] Single-file ops (rotate/watermark/combine/delete/reorder/split/images/text/print) run on a
      background `QThread` via `SingleOpWorker` + `MainWindow._run_async`.
- [x] Watermark opacity uses a validated `QDoubleSpinBox` (0.0–1.0) instead of free-text parsing.
- [x] Removed the duplicated hard-coded action lists (reuse `OUTPUT_DIR_ACTIONS` / `OFFICE_ACTIONS`).

## 7. Behavior / UX improvements ✅ (done)

- [x] Atomic writes: outputs are written to a temp file then `os.replace`d into place
      (`_atomic_write_bytes` / `_atomic_write_text` / `_atomic_write_pdf`).
- [x] `images_to_pdf` streams one image at a time (no longer loads all images into memory) and
      sizes each page to the image aspect ratio.
- [x] `print_pdf` documented as best-effort/"submitted" (it does not confirm printing).
- [x] `--verbose` / `--quiet` global flags and the `PDF_TOOL_LOG_LEVEL` environment variable.
- [x] `--overwrite` / `--skip-existing` and `--dry-run` global flags (the default now
      refuses to clobber an existing output).
- [x] Cross-platform LibreOffice (`soffice --headless`) fallback for Office conversion
      (used automatically when Office COM is unavailable).

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
- [x] `pre-commit` hook added (`.pre-commit-config.yaml`).
- [x] New features: page extract (`edit extract-pages`), page insert (`edit insert-pages`)
      and metadata read/write (the `metadata` command).
- [ ] PDF→image conversion (needs a renderer such as PyMuPDF/pdf2image) — **deferred**.
- [ ] True redaction (content removal) — **deferred** (an overlay-only approach would be
      misleading and unsafe).

## 10. GUI usability & intuitiveness (planned)

> Makes the desktop GUI more intuitive. **Additive** to items 1–9 above.
> Status: ✅ done · ◐ partial · `[ ]` planned.

### 10.1 Current GUI assessment

- One `QMainWindow` with six flat tabs — Edit, Split, Combine, Convert, Batch, Print
  (`pdfeditor/gui.py:491-496`).
- The **Edit** tab stacks four independent group boxes (Rotate / Watermark / Delete Pages /
  Reorder), each re-asking for its own Input/Output (`gui.py:505-604`).
- Every operation restarts from zero — there is no notion of a "current document", so the same
  file path is entered repeatedly.
- Output is a manual *Save As* for every action (no sensible default).
- Page ranges are free-text (`1,3,5-7`) with no page count or validation.
- Multi-file inputs (Merge, Images→PDF) are `;`-joined line edits, so **Merge order** (which is
  significant) cannot be reordered.
- Feedback is inconsistent: single-file ops show modal dialogs, Batch uses an inline log; there
  is no global progress indicator or Cancel.

### 10.2 Priority overview (GUI)

| #  | Improvement                                                             | Impact | Effort | Status |
|----|-------------------------------------------------------------------------|--------|--------|--------|
| G0 | Modularize the GUI + add a pytest-qt test harness                       | High   | Medium | ✅     |
| G1 | Smart output defaults (overwrite / save-as-new)                          | High   | Medium | ✅     |
| G2 | Unified status bar + progress + Cancel; non-modal success feedback      | High   | Medium | ✅     |
| G3 | Batch cleanup: drive the stack from combo data, file-count preview, dry-run | Medium | Low  | ◐     |
| G4 | Page-range UX: page count, inline validation, thumbnail picker          | High   | Medium | `[ ]`  |
| G5 | Ordered file list (add/remove/reorder) for Merge and Images→PDF         | Medium | Low    | `[ ]`  |
| G6 | Real drop-target styling and drag-to-open                               | Low    | Low    | ◐      |
| G7 | Feature parity in the GUI: Extract Pages, Insert Pages, Metadata        | Medium | Low    | `[ ]`  |
| G8 | Document-centric redesign: left action sidebar + shared input/output    | High   | High   | `[ ]`  |
| G9 | Polish: theme, scrolling, settings persistence, shortcuts, labels, recents | Low | Medium | `[ ]`  |

### 10.3 Phased plan

- **Phase 0 — Enable refactor + test harness** (no visual change) — ✅ done
  - [x] Split `pdfeditor/gui.py` into `pdfeditor/gui/` (`__init__.py`, `widgets.py`,
        `workers.py`, `main_window.py`); update `pyproject.toml` packages.
  - [x] Add `pytest-qt` (dev dep) and `tests/test_gui_smoke.py` (offscreen window build).

- **Phase 1 — Tier 1: intuitive core** — ✅ mostly done
  - [x] Smart output defaults (`<stem>_<op>.<ext>`) auto-filled beside the source.
  - [x] Unified status bar + progress + Cancel (batch cancel is honoured between files).
  - [x] Non-modal success feedback (status-bar messages instead of dialogs).
  - [x] Retired `action_to_index`; the Batch stack is driven from combo order.
  - [ ] `OperationPanel` abstraction — **moved into Phase 3** (the document-centric redesign).

- **Phase 2 — Tier 2: workflow ergonomics** — ◐ in progress
  - [ ] `PageRangeField` (page count + inline validation + "All pages").
  - [ ] `OrderedFileList` for Merge / Images→PDF.
  - [x] Drop-target styling (dashed border + drag-over highlight).
  - [ ] Window-level drag-to-open.
  - [x] Batch: live file-count preview.
  - [ ] Batch: contextual options + Dry-run button.
  - [ ] GUI parity: Extract Pages, Insert Pages, Metadata form.

- **Phase 3 — Tier 1A: document-centric redesign**
  - [ ] Left action sidebar + `QStackedWidget` of panels; shared Input/Output bar.
  - [ ] "Current document" state so operations reuse the loaded input.
  - [ ] Optional `PdfPreview` + `PagePickerDialog` (PySide6 `QtPdf`, no new dependency).

- **Phase 4 — Tier 3: polish**
  - [ ] `gui/theme.py` stylesheet (spacing, primary/secondary buttons, light/dark).
  - [ ] `QScrollArea` wrapping + `QSettings` geometry persistence + HiDPI.
  - [ ] Shortcuts/mnemonics, tab order, default buttons.
  - [ ] Action-oriented labels, richer error guidance, "Open folder" affordances.
  - [ ] Recent-files list; menu bar + toolbar.

### 10.4 Key artifacts

| Module | Contents |
|--------|----------|
| `pdfeditor/gui/widgets.py` | `DropLineEdit`, `PathRow`, `OutputRow`, `OperationPanel`, `PageRangeField`, `OrderedFileList` |
| `pdfeditor/gui/workers.py` | `BatchWorker`, `SingleOpWorker` (+ cancellation) |
| `pdfeditor/gui/preview.py` | `PdfPreview`, `PagePickerDialog` (QtPdf) |
| `pdfeditor/gui/theme.py` | `apply_theme(app)` |
| `pdfeditor/gui/main_window.py` | `MainWindow` (sidebar + stacked panels, status, settings) |
| `tests/test_gui_smoke.py` | pytest-qt smoke / validation tests |

### 10.5 Risks & mitigations

- **Phase 3 is the largest rework** — mitigated by completing Phases 0–2 first, so Phase 3 is
  mostly wiring.
- **Preview rendering** — use PySide6's bundled `QtPdf` (`QPdfDocument` / `QPdfView`); no new
  runtime dependency. Fallback: `pypdfium2`.
- **GUI/CLI drift** — the GUI delegates validation and behavior to `pdfeditor.core` only.
- **Testing UI** — pytest-qt with `QT_QPA_PLATFORM=offscreen` in CI.

---

## Suggested order of work

1. [x] `.gitignore` + untrack `__pycache__`.
2. [x] `pyproject.toml` + `tests/` (pure helpers + integration tests).
3. [x] Package restructure with CLI/GUI entry points; removed import-time logging config.
4. [x] De-duplicated validation; moved GUI single-file operations onto a worker thread.
5. [x] Hardened Office/COM (context manager, temp dirs, watchdog/timeout).

### Open follow-ups

- PDF→image conversion (requires a rendering dependency such as PyMuPDF or pdf2image).
- True redaction (content removal).
- GUI improvements G0–G9 (see [Section 10](#10-gui-usability--intuitiveness-planned)).

