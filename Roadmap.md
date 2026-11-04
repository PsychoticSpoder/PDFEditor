# PDFEditor — Roadmap & Improvement Recommendations

This document captures a code review of the PDF Toolkit (`pdf_tool.py`, `pdf_gui.py`,
`analyze_excel.py`) and lists concrete, prioritized improvements. Items are ordered by
impact; each entry notes the affected files/functions and a concrete action.

## Summary of current state

The project is a single-purpose PDF toolkit with two entry points and one diagnostic script:

- **`pdf_tool.py`** (~1,540 lines) — core PDF operations *and* the `argparse` CLI in the same module.
- **`pdf_gui.py`** (~1,170 lines) — PySide6 desktop GUI that imports functions from `pdf_tool`.
- **`analyze_excel.py`** — one-off Excel/COM diagnostic with a hardcoded path.
- **`README.md`**, **`requirements.txt`** — no packaging, no tests, no lint/type config.

The functionality is genuinely useful (rotate/watermark/split/combine/convert/print + batch,
plus Windows Word/Excel→PDF via COM).

## Priority overview

| # | Area                                   | Impact | Effort |
|---|----------------------------------------|--------|--------|
| 1 | Add a test suite                       | High   | Medium |
| 2 | Package & restructure the project      | High   | Medium |
| 3 | Repo hygiene (.gitignore, untrack pyc) | High   | Low    |
| 4 | De-duplicate CLI ↔ GUI validation      | Medium | Low    |
| 5 | Office/COM robustness                  | High   | Medium |
| 6 | Move GUI heavy work off main thread    | High   | Medium |
| 7 | Behavior / UX improvements             | Medium | Medium |
| 8 | Docs & security accuracy               | Medium | Low    |
| 9 | Lower priority / nitpicks              | Low    | Low    |

---

## 1. Add a test suite (there are none)

The README says "Run tests (when available)" — they don't exist. This is the single biggest gap,
especially given the fragile Office/Excel logic. Many functions are pure and trivially testable.

**Target the pure helpers:**

- `_parse_page_range` / `_parse_page_order` (`pdf_tool.py:188-239`) — boundary cases (`0`,
  `total+1`, reversed ranges `5-1`, duplicates, empty).
- `_collect_input_files` (79–96), `_is_office_temp_file` (99–101), `_resolve_workers` (126–131),
  `_is_blank_page` (1016–1054).

**Integration tests** using `tmp_path` + reportlab/pypdf to build tiny PDFs and exercise
`rotate_pdf`, `split_pdf`, `combine_pdfs`, `delete_page_range`, `reorder_pages`,
`watermark_pdf`, `images_to_pdf`, `pdf_to_text`.

**Actions:**

- Add a `tests/` package.
- Add a `pyproject.toml` with `[tool.pytest.ini_options]` and `pytest-cov`.
- Add a GitHub Actions workflow.
- Test Office paths with COM mocked.

## 2. Package and restructure the project

Everything lives in two flat, ~1,000+ line modules and relies on files being in the same
directory (the GUI does `from pdf_tool import ...`, which only works when run from the folder).

**Target layout:**

```
pdfeditor/
  __init__.py
  core/        # pdf_ops.py, office.py, ranges.py, io_utils.py
  cli.py       # build_parser + main (thin)
  gui/         # main_window.py, workers.py
pyproject.toml # metadata, deps, entry points: pdf-tool, pdf-editor-gui
```

**Actions:**

- Add **`pyproject.toml`** with console-script entry points so users run `pdf-tool` instead of
  `py pdf_tool.py`.
- Enable `pip install -e .`, fix import fragility, and remove the duplicated
  `logging.basicConfig` at import time (it currently has a global side effect that pollutes any
  app importing `pdf_tool` — `pdf_tool.py:20`, `pdf_gui.py:61`). Configure logging in `main()`
  / a `--verbose` flag instead.

## 3. Repo hygiene (quick wins)

- **No `.gitignore`, and compiled artifacts are committed.** `git ls-files` shows
  `__pycache__/pdf_gui.cpython-313.pyc` and `__pycache__/pdf_tool.cpython-313.pyc` are tracked,
  and `.venv/` is present but unignored.

**Actions:**

- Add a `.gitignore` (`__pycache__/`, `*.py[cod]`, `.venv/`, `dist/`, `build/`, `*.egg-info/`).
- `git rm -r --cached __pycache__`.

## 4. De-duplicate CLI ↔ GUI validation

The GUI re-implements path validation that already exists in the core
(`_existing_file_from_input`, `_output_file_from_input`, `_existing_dir_from_input`,
`_output_dir_from_input` at `pdf_gui.py:184-219` duplicate `_validate_existing_file` /
`_validate_output_path` etc. at `pdf_tool.py:53-76`). Likewise the set of "actions that need an
output dir" is hard-coded twice (`pdf_gui.py:1032-1041` and `876-885`) and the Office
worker-forcing logic twice.

**Actions:**

- Move validation and action metadata into the core so the GUI is a thin layer.

## 5. Office/COM robustness (the most fragile area)

- **COM lifecycle is repeated 4×** (`word_to_pdf`, `excel_to_pdf`, `batch_word_to_pdf`,
  `batch_excel_to_pdf` each do `_initialize_com_if_available()` … `CoUninitialize()` in
  try/finally). Extract a `@contextmanager com_application("Word.Application")` helper.
- **Excel temp files are unsafe**: `output_dir / f"_{worksheet.Name}.pdf"` (`pdf_tool.py:898`)
  can collide with real files, break on sheet names containing invalid path chars, and pollutes
  the user's output folder. Use `tempfile.TemporaryDirectory()`.
- **No timeout** — a hung Word/Excel instance blocks forever. Consider a watchdog that kills the
  process.
- **Redundant import** inside `_combine_pdfs_in_order` (`pdf_tool.py:996`
  `from pypdf import PdfWriter, PdfReader`) despite top-level imports.
- **Blank-page heuristic silently discards pages** (`pdf_tool.py:1016-1054`) with hardcoded
  thresholds (`<30` chars, `<50` non-space, `≤3` lines). A page with an image/chart footer could
  be dropped. Make it opt-in and log every skipped page.

## 6. GUI runs heavy work on the main thread

Only **batch** operations use a `QThread` worker. Single-file actions — `run_rotate`,
`run_watermark`, `run_combine`, `run_split`, `run_images_to_pdf`, `run_pdf_to_text`
(`pdf_gui.py:901-1016`) — execute synchronously, freezing the UI on large PDFs.

**Actions:**

- Move singles onto the same worker pattern.
- Replace raw parsing like `float(self.watermark_opacity.text())` (`pdf_gui.py:921`) with a
  `QDoubleSpinBox`/validator.
- Reuse one `addItems(..., userData)` mapping instead of the manual `action_to_index` table
  (`pdf_gui.py:849-859`).

## 7. Behavior / UX improvements

- **Print is fire-and-forget**: `os.startfile(path, "print")` (`pdf_tool.py:441-450`) doesn't
  verify delivery, yet `batch_print_pdfs` reports "submitted for N files" as if successful. Use
  `win32print`/`ShellExecuteEx` or at least document the limitation.
- **`images_to_pdf` loads every image fully into memory** (`pdf_tool.py:408-418`) — OOM risk on
  large batches; it also assumes uniform page size and ignores DPI. Stream one image per page
  with reportlab and normalize page sizing.
- **No safety flags**: add `--overwrite`/`--skip-existing`, `--dry-run`, `--verbose`/`--quiet`.
  Currently outputs are written silently in place and can clobber inputs.
- **Atomic writes**: render to a temp file and `os.replace()` into place so a mid-operation
  failure can't leave a truncated/corrupt output.
- **Cross-platform Office conversion fallback**: Word/Excel are Windows-only; a LibreOffice
  `soffice --headless --convert-to pdf` fallback would make batch conversion work everywhere.

## 8. Docs & security accuracy

- The README's "Security Notes" claims "Paths are resolved to absolute paths to prevent
  traversal attacks" (`README.md:212`) — resolving a path does **not** prevent traversal or
  overwriting arbitrary files. Reword, and add real safeguards (explicit overwrite confirmation,
  output-inside-input checks).
- README tells users to "modify source code" to change log level (`README.md:189-192`) — replace
  with a `--verbose` flag / `PDF_TOOL_LOG_LEVEL` env var once item 2 is done.
- `analyze_excel.py:194` hardcodes a personal path
  (`C:\Users\SESA641998\Downloads\...`). Parameterize via `sys.argv` so it's portable and doesn't
  leak user info.

## 9. Lower priority / nitpicks

- Add `pyproject.toml` config for `black` / `ruff` / `mypy` (README references them but none are
  configured), plus a `pre-commit` hook.
- `MAX_WORKERS = 64` vs `_resolve_workers` auto-cap of 8 (`pdf_tool.py:36,128`) — document intent.
- Office `except Exception: pass` blocks everywhere hide real errors; at least log at `debug`.
- Consider first-class features users likely want: PDF→image, image→PDF with page sizing,
  extract/insert specific pages, redaction, and metadata editing.

---

## Suggested order of work

1. `.gitignore` + untrack `__pycache__` (minutes).
2. `pyproject.toml` + `tests/` for the pure functions, then integration tests (biggest
   correctness payoff).
3. Restructure into a package with CLI/GUI entry points; kill import-time `logging.basicConfig`.
4. De-duplicate validation and move GUI singles onto the worker thread.
5. Harden Office/COM (context manager, temp dirs, timeouts, LibreOffice fallback).

The fastest high-value first step is adding `.gitignore` plus a `pyproject.toml` and a `tests/`
suite covering the pure helpers and core PDF operations.


