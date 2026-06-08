# PDF Tool (Python)

This project provides a command-line app to:

- edit PDFs (rotate pages, add text watermark)
- split PDFs
- combine multiple PDFs
- convert images to PDF
- convert PDF to text
- print PDFs
- batch process folders of PDFs
- batch convert Microsoft Word and Excel files to PDF (Windows)

It also includes a desktop GUI built with PySide6.

## 1. Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

## 2. Usage

```powershell
py pdf_tool.py --help
```

## 3. Run GUI (PySide6)

```powershell
py pdf_gui.py
```

The GUI has tabs for Edit, Split, Combine, Convert, Batch, and Print.

The GUI also supports:

- drag-and-drop for most file and folder path fields
- a Batch tab for folder processing
- configurable worker threads for batch tasks
- live batch status, progress bar, and log output
- continue-on-error toggle for Office batch conversions

Word/Excel conversion notes:

- currently Windows-only
- requires Microsoft Word/Excel to be installed

### Edit

Rotate all pages by 90 degrees:

```powershell
py pdf_tool.py edit rotate -i .\input.pdf -o .\rotated.pdf -a 90
```

Rotate only selected pages:

```powershell
py pdf_tool.py edit rotate -i .\input.pdf -o .\rotated.pdf -a 180 -p "1,3,5-7"
```

Add watermark text:

```powershell
py pdf_tool.py edit watermark -i .\input.pdf -o .\watermarked.pdf -t "CONFIDENTIAL" --opacity 0.2 --font-size 56
```

Delete pages 2 and 5-7:

```powershell
py pdf_tool.py edit delete-pages -i .\input.pdf -o .\deleted.pdf -p "2,5-7"
```

Reorder all pages (example for a 3-page PDF):

```powershell
py pdf_tool.py edit reorder -i .\input.pdf -o .\reordered.pdf --order "3,1,2"
```

### Split

Split all pages into separate files:

```powershell
py pdf_tool.py split -i .\input.pdf -d .\split_output
```

Split only selected pages with custom prefix:

```powershell
py pdf_tool.py split -i .\input.pdf -d .\split_output -p "1,3,5-6" --prefix "chapter"
```

### Combine

```powershell
py pdf_tool.py combine -i .\first.pdf .\second.pdf .\third.pdf -o .\merged.pdf
```

Drag-and-drop friendly positional form (drop files directly after command):

```powershell
py pdf_tool.py dnd combine .\merged.pdf .\first.pdf .\second.pdf .\third.pdf
```

### Convert

Images to PDF:

```powershell
py pdf_tool.py convert images-to-pdf -i .\img1.png .\img2.jpg -o .\images.pdf
```

PDF to text:

```powershell
py pdf_tool.py convert pdf-to-text -i .\input.pdf -o .\output.txt
```

Drag-and-drop friendly positional form:

```powershell
py pdf_tool.py dnd pdf-to-text .\input.pdf .\output.txt
```

### Print

Print on default printer:

```powershell
py pdf_tool.py print -i .\input.pdf
```

Drag-and-drop friendly positional form:

```powershell
py pdf_tool.py dnd print .\input.pdf
```

### Batch Folder Processing

You can parallelize batch work with `--workers`.

- `--workers 0` means auto-select thread count (default)
- `--workers 1` forces single-threaded execution
- `--workers 4` (or higher) runs multiple files in parallel

Office note:

- Word/Excel to PDF batch conversion is forced to single-worker mode for COM stability.
- Word/Excel batch conversion automatically skips temporary Office lock files (for example `~$*.docx`).

Rotate all PDFs in a folder:

```powershell
py pdf_tool.py batch rotate --input-dir .\in --output-dir .\out --angle 90 --pattern "*.pdf" --workers 4
```

Watermark all PDFs in a folder:

```powershell
py pdf_tool.py batch watermark --input-dir .\in --output-dir .\out --text "CONFIDENTIAL" --opacity 0.2 --font-size 56 --workers 4
```

Delete pages in all PDFs in a folder:

```powershell
py pdf_tool.py batch delete-pages --input-dir .\in --output-dir .\out --pages "2,5-7" --workers 4
```

Reorder pages in all PDFs in a folder:

```powershell
py pdf_tool.py batch reorder --input-dir .\in --output-dir .\out --order "3,1,2" --workers 4
```

Split all PDFs in a folder:

```powershell
py pdf_tool.py batch split --input-dir .\in --output-dir .\out --pattern "*.pdf" --workers 4
```

Extract text from all PDFs in a folder:

```powershell
py pdf_tool.py batch pdf-to-text --input-dir .\in --output-dir .\out --workers 4
```

Print all PDFs in a folder:

```powershell
py pdf_tool.py batch print --input-dir .\in --workers 4
```

Batch convert Word files to PDF:

```powershell
py pdf_tool.py batch word-to-pdf --input-dir .\word_in --output-dir .\pdf_out --pattern "*.doc*" --workers 2
```

Batch convert Excel files to PDF:

```powershell
py pdf_tool.py batch excel-to-pdf --input-dir .\excel_in --output-dir .\pdf_out --pattern "*.xls*" --workers 2
```

Drag-and-drop friendly positional commands for Office conversion:

```powershell
py pdf_tool.py dnd word-to-pdf .\input.docx .\output.pdf
py pdf_tool.py dnd excel-to-pdf .\input.xlsx .\output.pdf
```

On Windows, the command uses the default application associated with PDF files.