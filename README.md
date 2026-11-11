# PDF Tool (Python)

This project provides a command-line app and desktop GUI to:

- Edit PDFs (rotate pages, add text watermark)
- Export pages as one PDF per file, one PDF per page, or documents of N pages each — for a
  single PDF or a whole folder of them (or combine every PDF's pages into a single file)
- Combine multiple PDFs
- Convert images to PDF
- Convert PDF to text (one file or a whole folder)
- Print PDFs
- Batch process folders of PDFs
- Convert Microsoft Word and Excel files to PDF (one file or a whole folder; Windows only)

It also includes a desktop GUI built with PySide6. A ready-made Windows `.exe` (no Python
needed) is attached to every release — see [Releases](../../releases), or §10 to build it
yourself.

## 1. Setup

### Prerequisites

- Python 3.9 or higher
- For Word/Excel conversion: Microsoft Office (Windows only)

### Installation

```powershell
# Create virtual environment
py -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Upgrade pip
py -m pip install --upgrade pip

# Install dependencies
py -m pip install -r requirements.txt

# Install as an editable package (adds the `pdf-tool` and
# `pdf-editor-gui` console commands and enables `python -m pdfeditor`)
py -m pip install -e .
```

### Optional: Development Setup

```powershell
# Install development dependencies
py -m pip install pytest pytest-cov pytest-qt black ruff mypy pre-commit

# Run tests
pytest

# Format code
black .

# Lint code
ruff check .

# Type checking
mypy pdfeditor

# Install the pre-commit hooks
pre-commit install
```

> **Tip:** run these commands with the virtual environment activated
> (`.\.venv\Scripts\Activate.ps1`). When it is inactive, `pytest`, `black` and `ruff` may
> resolve to globally installed copies. The GUI tests skip themselves when PySide6 or
> pytest-qt is missing; the rest of the suite still runs.

## 2. Command-Line Usage

### Basic Commands

```powershell
# Show help
py pdf_tool.py --help

# Rotate PDF pages
py pdf_tool.py edit rotate -i input.pdf -o output.pdf -a 90

# Add watermark
py pdf_tool.py edit watermark -i input.pdf -o output.pdf -t "CONFIDENTIAL" --opacity 0.3

# Split PDF into pages
py pdf_tool.py split -i input.pdf -d output_folder

# Combine multiple PDFs
py pdf_tool.py combine -i file1.pdf file2.pdf file3.pdf -o combined.pdf

# Extract text from PDF
py pdf_tool.py convert pdf-to-text -i input.pdf -o output.txt

# Extract specific pages into a new PDF
py pdf_tool.py edit extract-pages -i input.pdf -o pages.pdf -p "1,3,5-7"

# Insert pages from another PDF before base page 2
py pdf_tool.py edit insert-pages -i base.pdf --insert extra.pdf -o merged.pdf --at 2

# Read or write PDF metadata
py pdf_tool.py metadata -i input.pdf
py pdf_tool.py metadata -i input.pdf --set Title="Quarterly Report" -o tagged.pdf

# Print PDF
py pdf_tool.py print -i input.pdf
```

### Safety Flags & Dry Run

These global flags go *before* the sub-command and apply to every command:

```powershell
# Preview the plan without writing anything
pdf-tool --dry-run edit rotate -i input.pdf -o output.pdf -a 90

# Overwrite existing outputs (the default is to refuse to clobber a file)
pdf-tool --overwrite edit rotate -i input.pdf -o output.pdf -a 90

# Skip a command whose output already exists (exits 0)
pdf-tool --skip-existing split -i input.pdf -d out
```

By default the CLI refuses to overwrite an existing output file; pass `--overwrite`
or `--skip-existing` to change that.

### Batch Operations

```powershell
# Batch rotate all PDFs in a folder
py pdf_tool.py batch rotate --input-dir ./input --output-dir ./output --angle 90

# Batch watermark with custom opacity
py pdf_tool.py batch watermark --input-dir ./input --output-dir ./output --text "DRAFT" --opacity 0.2

# Batch convert Word to PDF
py pdf_tool.py batch word-to-pdf --input-dir ./docs --output-dir ./pdfs

# Batch extract text from all PDFs
py pdf_tool.py batch pdf-to-text --input-dir ./pdfs --output-dir ./text

# Batch with custom file pattern
py pdf_tool.py batch rotate --input-dir ./input --output-dir ./output --angle 180 --pattern "**/*.pdf"
```

### Drag-and-Drop Commands

```powershell
# Combine dropped PDFs
py pdf_tool.py dnd combine output.pdf file1.pdf file2.pdf

# Convert dropped images to PDF
py pdf_tool.py dnd images-to-pdf output.pdf image1.png image2.jpg

# Split dropped PDF
py pdf_tool.py dnd split input.pdf output_folder

# Convert dropped Word file
py pdf_tool.py dnd word-to-pdf input.docx output.pdf
```

## 3. Run GUI (PySide6)

```powershell
py pdf_gui.py
```

The GUI is a list of panels down the left:

- **Single PDF Editing**: the page organizer (below) — every page as a thumbnail, with every
  page operation in its toolbar and right-click menu
- **Bulk PDF Editing**: Process entire folders — rotate, watermark, delete pages, reorder and
  **Export Pages** (one PDF per file, one PDF per page, documents of N pages each, or one PDF
  combining every file's pages). Page files land straight in the output folder
- **Merge**: Merge multiple PDFs
- **Convert**: Images to PDF, PDF to text and Word/Excel to PDF — one at a time, picked from a
  dropdown so each conversion gets the whole panel — and each one runs on a single file or a
  whole folder. The Images→PDF pane's `Make` dropdown combines every listed image into one PDF,
  or writes one PDF per image into a folder
- **Print**: Print PDFs to default printer

The **Current PDF** bar is gone: the Single PDF Editing panel's own "PDF to edit" row is the
file picker. Dropping a PDF onto the window also loads it there.

### Page organizer

The **Single PDF Editing** panel embeds the organizer: every page of the document as a
draggable thumbnail grid, opened from the panel's own "PDF to edit" row. (**File → Organize
Pages…**, `Ctrl+Shift+O`, opens the same view in its own window when you want to edit a
second file.) From there you can:

- Drag pages — or a whole multi-select block — to reorder them
- Select pages with click, `Ctrl`+click, `Shift`+click or `Ctrl+A`, then right-click for
  the page actions: rotate (left/right/180°), delete, export, insert, watermark and
  metadata
- **Export Pages…** asks how to split the selection: one PDF, one PDF per page, or documents
  of N pages each (e.g. ten pages saved as five two-page PDFs). A split that does not divide
  evenly (ten pages as six-page documents leaves 6 + 4) is confirmed first. The Bulk PDF
  Editing panel's **Export Pages** action offers the same modes for a whole folder — plus one
  PDF combining every matching file's pages — and writes its page files directly into the
  output folder, named after their source PDF
- Selected thumbnails are outlined, and every "Page N" caption stays on the same line even
  when pages have different shapes
- Thumbnails keep each page's own shape (a rotated page previews as landscape) and are
  cached, so an edit only redraws the pages it actually changed
- Edits are held in memory and written to a file only when one is needed (Save As/export),
  so rotating, deleting and reordering stay instant even on very large PDFs
- Read the live page/selection count in the status line
- Use **Revert to Original** to discard the whole editing session

Every action is applied to a temporary copy, so the original file is only changed when
you use **Save As…**.

### GUI Features

- **Drag-and-drop**: Drop files/folders into most path fields, and drop a PDF anywhere on the
  window to open it in the Single PDF Editing panel. Every field takes only the file type it is
  for — the "PDF to edit" row, the Merge / Images→PDF / Print lists and the operation inputs
  all refuse a dropped spreadsheet or folder (with the "no drop" cursor, and a note under the
  lists) instead of handing it to pypdf. Merge's output path is filled in from the first input
- **Never overwrites by default**: when a chosen output name is already taken, the file is
  written beside it with a ` (1)`, ` (2)`, … suffix instead of clobbering the existing one
- **Progress tracking**: Live progress bar and status for batch operations
- **Error handling**: Continue-on-error option for bulk Word/Excel conversion
- **Multi-threading**: Configurable worker threads for batch tasks

## 4. Configuration & Defaults

| Setting | Default | Description |
|---------|---------|-------------|
| Watermark opacity | 0.25 | Range: 0.0 to 1.0 |
| Watermark font size | 48 | Range: 8 to 300 |
| Batch workers | 4 | Range: 1 to 64 |
| Glob pattern | `*.pdf` | File matching pattern |
| Word pattern | `*.doc*` | Word file matching |
| Excel pattern | `*.xls*` | Excel file matching |

## 5. Troubleshooting

### Common Issues

**`ModuleNotFoundError: No module named 'pdfeditor'` when running `pytest`**
- Run the tests with the virtual environment active (`.\.venv\Scripts\Activate.ps1`); with
  it inactive, a shell may resolve a global `pytest` that cannot see this package
- The repo root is put on `sys.path` for pytest by `pythonpath = ["."]` in `pyproject.toml`,
  so `pytest` works from a fresh clone; `py -m pip install -e .` also makes it importable

**GUI tests report `SKIPPED ... could not import 'pytestqt'`**
- pytest-qt and PySide6 are only needed by the GUI tests; the rest of the suite runs
  without them. Install them with `py -m pip install pytest-qt PySide6`

**"File not found" errors**
- Ensure file paths are absolute or relative to current directory
- Check file permissions
- Verify the file exists before running the command

**"Rotation angle must be a multiple of 90"**
- Valid angles: -360, -270, -180, -90, 0, 90, 180, 270, 360

**"Opacity must be between 0.0 and 1.0"**
- Valid range: 0.0 (transparent) to 1.0 (opaque)
- Recommended: 0.1 to 0.3 for watermarks

**Word/Excel conversion fails**
- Ensure Microsoft Office is installed (Windows). On other platforms install
  LibreOffice (`soffice`) to use the cross-platform fallback.
- Run with `--workers 1` for better stability
- Close Office applications before batch conversion
- Check for locked/temp files (starting with `~$`)
- A single hung conversion is killed automatically after ~120s
  (`OFFICE_OPERATION_TIMEOUT_SECONDS`)

**"No files matched pattern"**
- Verify the glob pattern matches your files
- Try `--pattern "*.PDF"` for uppercase extensions
- Use `--pattern "**/*.pdf"` for recursive search

**COM/Office errors on Windows**
- Restart the application
- Reduce batch size or use single-worker mode
- Ensure Office is properly activated

### Logging

The application logs to the console with timestamps. Control verbosity with the global
`--verbose`/`--quiet` flags or the `PDF_TOOL_LOG_LEVEL` environment variable — no source
edits required:

```powershell
pdf-tool --verbose edit rotate -i input.pdf -o output.pdf -a 90
pdf-tool --quiet split -i input.pdf -d out
$env:PDF_TOOL_LOG_LEVEL = "DEBUG"
```

### Performance Tips

- Use multiple workers (`--workers 8`) for CPU-bound operations
- Use single worker (`--workers 1`) for Office conversions
- Process large batches in chunks of 20-50 files
- Use SSD storage for better I/O performance

## 6. Page Range Syntax

Page ranges use 1-based indexing:

- Single page: `1`, `5`, `10`
- Multiple pages: `1,3,5,7`
- Range: `1-10`, `5-15`
- Combined: `1,3,5-7,10,12-15`

## 7. Security Notes

- Paths are resolved to absolute paths before use
- Outputs are written atomically (temporary file + atomic replace), so a failed
  operation cannot leave a partially written/corrupt file behind
- Office conversions run in isolated COM contexts
- Office export temporary files are created in a system temp directory and cleaned up automatically
- No data is sent to external services

## 8. License

[Add your license here]

## 9. Contributing

[Add contribution guidelines if applicable]

## 10. Portable Windows build (.exe)

Every tagged release carries a ready-made build, so users need no Python at all:

| Asset | What it is |
|-------|------------|
| `PDFEditor.exe` | The whole GUI in one file. Download it anywhere and double-click it — no installer, nothing to install first. |
| `PDFEditor-console.exe` | The same program with a console window, which prints the errors the normal build has nowhere to show. Useful for a bug report. |
| `*.sha256` | Checksums, so a download can be verified: `certutil -hashfile PDFEditor.exe SHA256`. |

Windows SmartScreen warns about an unknown publisher the first few times, because the downloads
are not code-signed yet: choose **More info** → **Run anyway**.

Some features still need something installed on the machine:

- **Word/Excel to PDF** uses COM, so Microsoft Office (or LibreOffice, picked up automatically as
  a fallback) has to be present. Everything else is self-contained.
- **Print** needs a PDF handler registered with Windows (a browser or reader is enough).
- The saved window size lives in the registry under `HKCU\Software\pdfeditor`, and a one-file
  build unpacks itself to a temporary folder on each launch, so the very first start is slower.

### Building it yourself

```powershell
# One-off: the packaging tool (it is not a runtime dependency of the app)
.venv\Scripts\python.exe -m pip install "pyinstaller>=6.11"

# The windowed build users download, then the troubleshooting build
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\PDFEditor.spec
$env:PDFEDITOR_CONSOLE = "1"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\PDFEditor.spec
Remove-Item Env:\PDFEDITOR_CONSOLE
```

Both exes land in `dist\`. What goes into them is defined once, in `packaging/PDFEditor.spec`:

- `PDFEDITOR_CONSOLE` only decides the name and whether a console is attached, so the build people
  download and the build they report bugs with are the same program.
- The exe's version metadata is generated from `pdfeditor.__version__`, so it cannot drift.
- The icon comes from `packaging/PDFEditor.ico`, drawn by `packaging/make_icon.py` if it ever needs
  a tweak.
- `win32com` and `pythoncom` are reached through `importlib` in `office.py`, which PyInstaller
  cannot see, so they are listed as hidden imports and pywin32's DLLs are collected out of
  `pywin32_system32` by hand.
- `PySide6.QtPdf` draws the page thumbnails. A bundle without it still starts and only reports
  "Thumbnails unavailable (QtPdf missing)", so `.github/workflows/release.yml` inspects the built
  archive for `PySide6/QtPdf.pyd` and `Qt6Pdf.dll` before publishing anything.

Tagging a commit runs that workflow (`git tag v0.1.0` then `git push main v0.1.0`): it runs the
test suite, builds both exes, verifies the bundle, writes the checksums and creates the release.
Running the workflow by hand builds the same exes as a workflow artifact without publishing
anything, which is the safe way to try a change to the spec.

