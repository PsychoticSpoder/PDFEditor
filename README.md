# PDF Tool (Python)

This project provides a command-line app and desktop GUI to:

- Edit PDFs (rotate pages, add text watermark)
- Split PDFs into individual pages
- Combine multiple PDFs
- Convert images to PDF
- Convert PDF to text
- Print PDFs
- Batch process folders of PDFs
- Batch convert Microsoft Word and Excel files to PDF (Windows only)

It also includes a desktop GUI built with PySide6.

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

# Optional: install as an editable package (adds the `pdf-tool` and
# `pdf-editor-gui` console commands and enables `python -m pdfeditor`)
py -m pip install -e .
```

### Optional: Development Setup

```powershell
# Install development dependencies
py -m pip install pytest pytest-cov black ruff mypy pre-commit

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

The GUI has tabs for:

- **Edit**: Rotate, watermark, delete pages, reorder pages
- **Split**: Split PDF into individual pages
- **Combine**: Merge multiple PDFs
- **Convert**: Images to PDF, PDF to text
- **Batch**: Process entire folders
- **Print**: Print PDFs to default printer

### Page organizer

**File → Organize Pages…** (`Ctrl+Shift+O`) opens one view showing every page as a
thumbnail. From there you can:

- Drag pages — or a whole multi-select block — to reorder them
- Select pages with click, `Ctrl`+click, `Shift`+click or `Ctrl+A`, then right-click for
  the page actions: rotate (left/right/180°), delete, extract, insert, split, watermark
  and metadata
- Read the live page/selection count in the status line
- Use **Revert to Original** to discard the whole editing session

Every action is applied to a temporary copy, so the original file is only changed when
you use **Save As…**.

### GUI Features

- **Drag-and-drop**: Drop files/folders into most path fields
- **Progress tracking**: Live progress bar and status for batch operations
- **Error handling**: Continue-on-error option for Office conversions
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
