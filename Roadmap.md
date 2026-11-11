# PDFEditor — Roadmap & Improvement Recommendations

> **Implementation status:** Items 1–8 are **implemented** (✅). Item 9 is **partially
> implemented** — page extract/insert and metadata editing shipped; PDF→image and
> redaction remain open `[ ]` (deferred). All automated tests pass (`pytest -q`).
> The GUI usability plan in [Section 10](#10-gui-usability--intuitiveness-planned) is implemented (✅).
> So is the packaging/release plan in Section 11 (✅).

## Summary of current state (updated)

The project is now an installable `pdfeditor` package:

- **`pdfeditor/core/`** — `constants.py`, `io_utils.py`, `ranges.py`, `pdf_ops.py`, `office.py`
- **`pdfeditor/cli.py`** — CLI (`pdf-tool` console script / `python -m pdfeditor`)
- **`pdfeditor/gui.py`** — PySide6 GUI (`pdf-editor-gui` console script)
- **`pdf_tool.py`, `pdf_gui.py`** — thin backward-compatibility shims
- **`tests/`** + **`.github/workflows/ci.yml`** — pytest suite and CI
- **`packaging/`** + **`.github/workflows/release.yml`** — PyInstaller build of the portable
  Windows `.exe` and the tag-triggered release workflow
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
| G3 | Batch cleanup: drive the stack from combo data, file-count preview, dry-run | Medium | Low  | ✅     |
| G4 | Page-range UX: page count, inline validation, thumbnail picker          | High   | Medium | ◐      |
| G5 | Ordered file list (add/remove/reorder) for Merge and Images→PDF         | Medium | Low    | ✅     |
| G6 | Real drop-target styling and drag-to-open                               | Low    | Low    | ✅     |
| G7 | Feature parity in the GUI: Extract Pages, Insert Pages, Metadata        | Medium | Low    | ✅     |
| G8 | Document-centric redesign: left action sidebar + shared input/output    | High   | High   | ✅     |
| G9 | Polish: theme, scrolling, settings persistence, shortcuts, labels, recents | Low | Medium | ✅     |
| G10 | Unified page organizer: thumbnail grid, multi-select, all page ops, metadata | High | Medium | ✅     |

### 10.3 Phased plan

- **Phase 0 — Enable refactor + test harness** (no visual change) — ✅ done
  - [x] Split `pdfeditor/gui.py` into `pdfeditor/gui/` (`__init__.py`, `widgets.py`,
        `workers.py`, `main_window.py`); update `pyproject.toml` packages.
  - [x] Add `pytest-qt` (dev dep) and `tests/test_gui_smoke.py` (offscreen window build).

- **Phase 1 — Tier 1: intuitive core** — ✅ done
  - [x] Smart output defaults (`<stem>_<op>.<ext>`) auto-filled beside the source.
  - [x] Unified status bar + progress + Cancel (batch cancel is honoured between files).
  - [x] Non-modal success feedback (status-bar messages instead of dialogs).
  - [x] Retired `action_to_index`; the Batch stack is driven from combo order.
  - [x] `OperationPanel` abstraction — every file-in/file-out operation reuses it.

- **Phase 2 — Tier 2: workflow ergonomics** — ✅ done
  - [x] `PageRangeField` (page count + inline validation + "All pages").
  - [x] `OrderedFileList` for Merge / Images→PDF / Print.
  - [x] Drop-target styling (dashed border + drag-over highlight).
  - [x] Window-level drag-to-open (drop a PDF onto the window).
  - [x] Batch: live file-count preview.
  - [x] Batch: Dry Run button (workers/continue-on-error already enable contextually).
  - [x] GUI parity: Extract Pages, Insert Pages, Metadata form.

- **Phase 3 — Tier 1A: document-centric redesign** — ✅ done
  - [x] Left action sidebar + `QStackedWidget` of operation panels (replaces the flat tab bar).
  - [x] Shared "current document" bar (open once; auto-fills every operation's input + outputs).
  - [x] `PdfPreview` first-page thumbnail + page count, wired to the current document.
  - [x] `PagePickerDialog` thumbnail grid (via bundled QtPdf) → "Pick…" on page-range fields.
  - [x] Window-level drag-to-open (drop a PDF onto the window).

- **Phase 4 — Tier 3: polish** — ✅ done
  - [x] `gui/theme.py` stylesheet (spacing, group titles, hover states).
  - [x] `QScrollArea` wrapping + `QSettings` window-geometry persistence.
  - [x] Action-oriented button labels ("Rotate", "Merge PDFs", "Extract Text", ...).
  - [x] Menu bar with mnemonics + shortcuts (`Ctrl+O`, `Ctrl+Q`).
  - [x] Status-bar "Open folder" affordance + richer error guidance.
  - [x] Recent-documents combo, persisted via `QSettings`.

- **Phase 5 — Tier 2: unified page organizer** — ✅ done
  - [x] `PageOrganizerDocument` (`core/organizer.py`): a Qt-free working-copy session that
        reuses the core ops (reorder/rotate/delete/watermark/insert/metadata + extract/split)
        against a temp copy, with `save_as` and `revert`.
  - [x] `PageGridWidget` (`gui/widgets.py`): the drag-reorder grid extracted from the
        reorder dialog, now with multi-select block dragging; shared by the reorder dialog,
        the picker and the organizer.
  - [x] `PageOrganizerDialog` (`gui/organizer.py`): thumbnail grid with click/`Ctrl`/`Shift`/
        `Ctrl+A` selection, drag-to-reorder, a toolbar + right-click menu covering rotate,
        delete, extract, insert, split, watermark, metadata, revert and save-as.
  - [x] `watermark_pdf(..., pages=...)` so a watermark can target a selection only.
  - [x] The organizer is embedded in the **Single PDF Editing** panel; the former
        File → **Organize Pages…** (`Ctrl+Shift+O`) menu entry in `MainWindow` was removed.
  - [x] Tests: `tests/test_organizer.py` (working-copy session) and
        `tests/test_gui_organizer.py` (grid + dialog actions).
  - [x] Incremental thumbnails: pages are cached by appearance, so an edit re-renders only
        the pages it changed and a drag reorder re-renders nothing at all.
  - [x] Aspect-correct previews: each page renders at its own ratio (and its cell resizes to
        match), so rotated/landscape pages are no longer squashed into a portrait cell.
  - [x] A thumbnail **zoom** slider in the Single PDF Editing panel scales the cells and
        re-renders the pages at the higher resolution, so page contents can be read in detail.
        Cell sizes come from each page's aspect ratio, so a zoom relayouts the grid once and
        leaves no stale, overlapping cells; the zoom change never blocks the GUI thread and a
        finished render settles the layout immediately.
  - [x] Lazy rendering: a document past a page threshold renders only the pages around the
        viewport (plus a margin) and re-renders them on scroll, dropping the thumbnails that
        scroll far away. Opening or zooming a 500-page file no longer renders every page.
  - [x] The zoom only re-renders once the mouse button is released — whether the press landed
        on the handle or on the bar — and an operation's status message (e.g. "Reordered to
        2,1,3,4.") falls back to the page summary on its own rather than waiting for the next
        render (which, with lazy rendering, used to need a scroll).
  - [x] A drag now commits the new order to the working copy, so Save As writes the order
        that is on screen.
  - [x] A drop also relayouts the grid (`PageGridWidget.relayout`): a moved item keeps its own
        cell width, and QListView otherwise keeps drawing the cells at the offsets of the items
        they traded places with — which left a row's last thumbnails hanging off the right edge
        until the window was resized.
  - [x] A reorder no longer blocks the GUI thread: the render in flight is cancelled without
        waiting for it (the same non-blocking stop the zoom uses) and the new order is written to
        the working copy on a worker thread (`WorkingCopyWriter`), so the next scroll, zoom or
        drag finds the file up to date instead of paying for the whole rewrite. The core's edit
        bookkeeping is locked (`PageOrganizerDocument.has_pending_edits` / `_take_pending`), and
        a write that fails puts the pending edits back instead of dropping them.
  - [x] The status line can no longer decide how wide the window is. A word-wrapped `QLabel`
        reports its widest *unbreakable* run as a minimum width, and the reorder summary
        ("Reordered to 2,3,4,…,43,1.") is one such run — 1440 px for 43 pages. That minimum
        propagated into the panel and the window: with a maximized window Qt could not grow it,
        so the page grid was handed a width far past the right edge and every page landed on one
        single row of thumbnails (in a resizable window it grew the window instead) until the
        window was resized. `_never_widen()` ignores the label's horizontal hint, leaving the
        width to the layout while the height still follows the wrapped text.
  - [x] Extract and Split merged into one **Export Pages…** action: one PDF, one PDF per page,
        or documents of N pages each (`split_pdf(..., chunk_size=N)`), so ten pages can be
        saved as five two-page PDFs.
  - [x] A chunked export that would leave a short final document (ten pages as six-page
        documents means 6 + 4) warns the user and lets them continue, instead of splitting
        silently.
  - [x] Edits are recorded and then written in a single pass, only when a real file is
        needed, so large PDFs stay responsive — and rotating transforms the cached preview
        instead of re-rendering it.
  - [x] Grid cells share one height so the "Page N" captions line up, and selected
        thumbnails get an outline.
  - [x] One **Single PDF Editing** panel replaces the eight per-operation panels (Rotate,
        Watermark, Delete Pages, Reorder, Extract Pages, Insert Pages, Metadata and Split):
        it embeds the organizer widget, which now owns its own "PDF to edit" row. The shared
        "Current PDF" bar is only shown for that panel.
  - [x] `PdfThumbnailLoader` keeps itself alive until its worker thread really stops, so a
        render can never outlive the widget that asked for it (Qt aborts if a running
        QThread is destroyed).
  - [x] Removed the shared "Current PDF" bar (field, Open PDF button, recent-documents
        dropdown and the `PdfPreview` first-page thumbnail): the panel's own "PDF to edit" row
        is now the single file picker, and dropping a PDF onto the window loads it there.
  - [x] Removed the **File → Open PDF…** (`Ctrl+O`) menu entry as well: the panel's "PDF to
        edit" row is the one file picker, so the menu kept only Quit.
  - [x] Sidebar tidy-up: **Merge / Combine** renamed to **Merge**, **Batch** renamed to
        **Bulk PDF Editing** and moved directly under Single PDF Editing, and the **Print** panel
        now takes its PDFs as an ordered list so the print order can be arranged first.
  - [x] **Bulk PDF Editing** is now PDF-only: Print moved out (the Print panel already takes a
        list) and PDF to Text, Word to PDF and Excel to PDF moved to **Convert**. Word/Excel
        conversion there uses `ConversionPanel`, so each one handles a single file or a whole
        folder.
  - [x] The bulk **Split** action is now **Export Pages** and offers the same modes as the Single
        PDF Editing panel — one PDF per input file, one PDF per page, or documents of N pages
        each — through the shared `ExportModeSelector`, plus one PDF combining every matching
        file's pages (`batch_split_pdfs(..., mode=..., chunk_size=...)`).
  - [x] Bulk page exports no longer nest a folder per input PDF: every file lands in the output
        folder itself, named after its source (`report_page_001.pdf`). A filename prefix keeps
        the source name when several PDFs are exported at once, so nothing is overwritten.
  - [x] The ordered input lists (Merge, Images→PDF, Print) only take the file type they are for:
        a dropped folder or spreadsheet is refused with a note under the list instead of
        reaching pypdf (which used to fail with "Stream has ended unexpectedly"). `run_combine`
        / `run_images_to_pdf` / `run_print` report the same thing as a clear error, and
        window-level drag-to-open only accepts PDFs — dropping a spreadsheet no longer switches
        to the Single PDF Editing panel and reports a pypdf traceback.
  - [x] The same filtering now covers every path field: `DropLineEdit(filter_text=...)` refuses a
        drop that is not the type the field is for (so an xlsx dropped on "PDF to edit" or on any
        operation's PDF input is refused with the "no drop" cursor), and
        `PageOrganizerWidget.load` reports "Not a PDF file" / "could not be read as a PDF" — as a
        one-line warning rather than a pypdf traceback — when a wrong file gets that far.
  - [x] **Convert** shows one conversion at a time, selected from a "Conversion" dropdown
        (Images to PDF, PDF to Text, Word to PDF, Excel to PDF). Stacking all four used to crowd
        them; the chosen pane now owns the panel, and the Images→PDF list gets its spare height
        instead of two rows.
  - [x] Images→PDF can make one PDF for every image or one PDF per image (`Make` dropdown); the
        output row swaps between a filename and a folder, and images that would become the same
        file (`photo.png` + `photo.jpg`) are reported instead of overwriting each other.
  - [x] Images→PDF's first `Make` choice is now **"Combine all images into one PDF"**, not "One
        PDF for every image" — that could be read as one PDF *per* image, i.e. the other choice.
        Both options now name their result: combine, or one PDF per image.
  - [x] `ConversionPanel`'s bulk form no longer leaves unused fields floating over the
        "Input Folder" label: a field that is never added to the layout sits in the page's
        top-left corner, on top of the first row's label, so it is hidden instead.
  - [x] Merge's "Output PDF" row is now really auto-filled from the first input in the list
        (`a.pdf` → `a_combined.pdf`) until the user puts a path of their own there, matching what
        the placeholder has always promised (and what Images→PDF does too).

### 10.4 Key artifacts

| Module | Contents |
|--------|----------|
| `pdfeditor/gui/widgets.py` | `DropLineEdit`, `PathRow`, `OutputRow`, `OperationPanel`, `PageRangeField`, `OrderedFileList`, `PageGridWidget` |
| `pdfeditor/gui/workers.py` | `BatchWorker`, `SingleOpWorker` (+ cancellation) |
| `pdfeditor/gui/preview.py` | `PdfThumbnailLoader`, `PagePickerDialog`, `PageReorderDialog` (QtPdf) |
| `pdfeditor/gui/organizer.py` | `PageOrganizerDialog`, `MetadataDialog`, `WatermarkDialog` |
| `pdfeditor/core/organizer.py` | `PageOrganizerDocument` (working-copy editing session) |
| `pdfeditor/gui/theme.py` | `apply_theme(app)` |
| `pdfeditor/gui/main_window.py` | `MainWindow` (sidebar + stacked panels, status, settings) |
| `tests/test_gui_smoke.py` | pytest-qt smoke / validation tests |
| `tests/test_gui_organizer.py` | pytest-qt tests for the page grid and organizer |
| `tests/test_organizer.py` | Tests for the working-copy organizer session |
| `packaging/PDFEditor.spec` | PyInstaller recipe for `PDFEditor.exe` and `PDFEditor-console.exe` |
| `packaging/make_icon.py` | Draws `packaging/PDFEditor.ico`, the icon the exe ships with |
| `packaging/report_bundle_size.py` | Prints where a built exe's size goes (Qt, Python, reportlab …) |
| `.github/workflows/release.yml` | Tag-triggered build: tests, both exes, bundle check, release |

## 11. Portable Windows build & releases ✅ (done)

- [x] `packaging/PDFEditor.spec` builds the GUI into one self-contained `PDFEditor.exe`, and — with
      the `PDFEDITOR_CONSOLE` environment variable set — into `PDFEditor-console.exe`, the same
      program with a console so a user hitting a problem can send the output. Both carry the app's
      version metadata (generated from `pdfeditor.__version__`, so it cannot drift) and the icon
      drawn by `packaging/make_icon.py`.
- [x] The spec collects what PyInstaller cannot see on its own: `win32com`/`pythoncom` are reached
      through `importlib` in `office.py`, and their DLLs live in `pywin32_system32` rather than
      inside an importable package; `PySide6.QtPdf` (the page thumbnails) is a hidden import,
      because a bundle without it still starts and only loses thumbnails.
- [x] `.github/workflows/release.yml` runs on a `v*` tag: it runs the suite, builds both exes,
      inspects the archive for `PySide6/QtPdf.pyd`, `Qt6Pdf.dll`, `qwindows.dll` and the pywin32
      modules, writes `.sha256` checksums and creates the release. A manual run builds the same
      exes as a workflow artifact and publishes nothing.
- [x] README §10 documents the download, the local build and what a single-file build does *not*
      solve: Word/Excel conversion still needs Office or LibreOffice on the machine, the download
      is unsigned (so SmartScreen warns and the checksums matter), and a one-file build unpacks
      itself to a temporary folder on first launch.
- [x] `packaging/report_bundle_size.py` prints the size breakdown of a built exe (read with
      PyInstaller's own archive reader: Qt 6, the Python runtime, reportlab, Pillow, pywin32 …), and
      the release workflow appends it to the run summary, so the ~50 MB is explained by the artifact
      instead of assumed.
- [x] The GUI depends on `PySide6-Essentials` + `PySide6-Pdf` instead of the `PySide6`
      metapackage, which would also install PySide6-Addons (33 modules the app cannot reach) and
      PySide6-WebEngine. `PySide6-Pdf` is what carries `QtPdf`, so the thumbnails keep working, and
      the spec's excludes are the belt to that braces. Both wheels need PySide6 6.12, so the
      project now requires Python 3.10.
- [x] `packaging/PDFEditor.spec` trims the files the Qt/Pillow/pywin32 hooks collect but the app can
      never load (`opengl32sw.dll`, Pillow's AVIF codec, Qt's `translations/*.qm`, pywin32's MFC/Tk
      extras). That is where the download actually shrank: measured with
      `packaging/report_bundle_size.py`, a 48.9 MB build became 35.2 MB, with 196 bundled entries
      down to 93, and the exe still starts.

### 11.1 Risks & mitigations

- **Unsigned download** — SmartScreen warns until the exe earns reputation. Mitigated for now by
  publishing SHA-256 checksums and saying so in the release notes; a code-signing certificate (or
  a free OSS service such as SignPath) is the real fix.
- **Antivirus false positives on one-file bundles** — mitigated by not packing with UPX and by
  publishing the checksums; a `.zip` of an unpacked build is the fallback if a user is blocked.
- **Office still required for Word/Excel → PDF** — COM cannot be bundled. The app already says so
  at the point of use, and the release notes repeat it so it is not reported as a bug.


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
