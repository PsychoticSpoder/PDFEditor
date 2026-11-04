"""Diagnostic script to analyze Excel file structure and identify page break issues."""
import sys
from pathlib import Path

import win32com.client as win32

def analyze_excel_file(file_path: str):
    """Analyze an Excel file to understand its structure and print settings."""
    print(f"\n{'='*80}")
    print(f"Analyzing: {file_path}")
    print(f"{'='*80}\n")
    
    excel = win32.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    
    try:
        workbook = excel.Workbooks.Open(str(file_path), ReadOnly=True)
        
        print(f"Worksheets: {workbook.Worksheets.Count}")
        print(f"File: {workbook.Name}\n")
        
        for i, worksheet in enumerate(workbook.Worksheets, 1):
            print(f"\n{'-'*80}")
            print(f"Worksheet {i}: {worksheet.Name}")
            print(f"{'-'*80}")
            
            # Get UsedRange info
            used_range = worksheet.UsedRange
            used_rows = used_range.Rows.Count
            used_cols = used_range.Columns.Count
            print(f"UsedRange Address: {used_range.Address}")
            print(f"UsedRange Rows: {used_rows:,}")
            print(f"UsedRange Columns: {used_cols:,}")
            print(f"UsedRange Total Cells: {used_rows * used_cols:,}")
            
            # Count cells with different content types
            constants_count = 0
            formulas_count = 0
            empty_formatted_count = 0
            
            try:
                constants = worksheet.UsedRange.SpecialCells(-4123)  # xlCellTypeConstants
                constants_count = constants.Count if constants else 0
            except Exception:
                constants_count = 0
            
            try:
                formulas = worksheet.UsedRange.SpecialCells(-4124)  # xlCellTypeFormulas
                formulas_count = formulas.Count if formulas else 0
            except Exception:
                formulas_count = 0
            
            total_with_content = constants_count + formulas_count
            empty_formatted = (used_rows * used_cols) - total_with_content
            
            print(f"\nCell Content Analysis:")
            print(f"  Cells with constants (values): {constants_count:,}")
            print(f"  Cells with formulas: {formulas_count:,}")
            print(f"  Total cells with actual content: {total_with_content:,}")
            print(f"  Empty formatted cells (white fill): {empty_formatted:,}")
            
            if empty_formatted > total_with_content:
                print(f"\n⚠️  CRITICAL: {empty_formatted:,} empty formatted cells are bloating the UsedRange!")
                print(f"   This is {(empty_formatted / max(total_with_content, 1)):.1f}x more than actual data!")
            
            # Try Find method with different LookIn values
            print(f"\nFind Method Tests:")
            cell1 = None
            cell2 = None
            
            # Test 1: Find with xlFormulas
            try:
                cell1 = worksheet.Cells.Find(
                    What="*",
                    After=worksheet.Range("A1"),
                    LookAt=-4163,
                    LookIn=-4163,  # xlFormulas
                    SearchOrder=1,
                    SearchDirection=2,
                )
                if cell1:
                    print(f"  ✓ Find(xlFormulas) - Last cell: {cell1.Address} (Row {cell1.Row}, Col {cell1.Column})")
                else:
                    print(f"  ✗ Find(xlFormulas) - No cells found")
            except Exception as e:
                print(f"  ✗ Find(xlFormulas) - Error: {type(e).__name__}")
            
            # Test 2: Find with xlValues
            try:
                cell2 = worksheet.Cells.Find(
                    What="*",
                    After=worksheet.Range("A1"),
                    LookAt=-4163,
                    LookIn=-4123,  # xlValues
                    SearchOrder=1,
                    SearchDirection=2,
                )
                if cell2:
                    print(f"  ✓ Find(xlValues) - Last cell: {cell2.Address} (Row {cell2.Row}, Col {cell2.Column})")
                else:
                    print(f"  ✗ Find(xlValues) - No cells found")
            except Exception as e:
                print(f"  ✗ Find(xlValues) - Error: {type(e).__name__}")
            
            # Calculate recommended print area
            if cell2:
                recommended_area = f"$A$1:${chr(64+cell2.Column)}${cell2.Row}"
                print(f"\n  Recommended Print Area: {recommended_area}")
                print(f"  (Based on Find(xlValues) last cell)")
            elif cell1:
                recommended_area = f"$A$1:${chr(64+cell1.Column)}${cell1.Row}"
                print(f"\n  Recommended Print Area: {recommended_area}")
                print(f"  (Based on Find(xlFormulas) last cell)")
            else:
                print(f"\n  ✗ Cannot determine data bounds - Find methods failed")
            
            # Check for manual page breaks
            h_page_breaks = worksheet.HPageBreaks.Count
            v_page_breaks = worksheet.VPageBreaks.Count
            
            print(f"\nPage Break Analysis:")
            if h_page_breaks > 0 or v_page_breaks > 0:
                print(f"  ⚠️  Manual Page Breaks Found:")
                print(f"     Horizontal: {h_page_breaks}")
                print(f"     Vertical: {v_page_breaks}")
                
                # List first few page break locations
                if h_page_breaks > 0 and h_page_breaks <= 10:
                    for j in range(1, h_page_breaks + 1):
                        try:
                            pb = worksheet.HPageBreaks.Item(j)
                            print(f"     H-Break {j}: Before row {pb.Location.Row}")
                        except Exception:
                            pass
                
                if v_page_breaks > 0 and v_page_breaks <= 10:
                    for j in range(1, v_page_breaks + 1):
                        try:
                            pb = worksheet.VPageBreaks.Item(j)
                            print(f"     V-Break {j}: Before column {pb.Location.Column}")
                        except Exception:
                            pass
            else:
                print(f"  ✓ No manual page breaks")
            
            # Check current print area
            try:
                print_area = worksheet.PageSetup.PrintArea
                if print_area:
                    print(f"\nCurrent Print Area: {print_area}")
                else:
                    print(f"\nNo Print Area set (will use UsedRange)")
            except Exception as e:
                print(f"\nError reading Print Area: {e}")
            
            # Check page setup
            ps = worksheet.PageSetup
            print(f"\nPage Setup Configuration:")
            print(f"  Orientation: {'Landscape' if ps.Orientation == 1 else 'Portrait'}")
            print(f"  Zoom: {ps.Zoom if ps.Zoom else 'Auto (FitToPages)'}%")
            print(f"  FitToPagesWide: {ps.FitToPagesWide}")
            print(f"  FitToPagesTall: {ps.FitToPagesTall}")
            print(f"  Paper Size: {ps.PaperSize}")
            print(f"  Center Horizontally: {ps.CenterHorizontally}")
            print(f"  Center Vertically: {ps.CenterVertically}")
            
            # Provide recommendations
            print(f"\n{'='*40}")
            print(f"RECOMMENDATIONS:")
            print(f"{'='*40}")
            
            if h_page_breaks > 0 or v_page_breaks > 0:
                print(f"1. Remove ALL manual page breaks (found {h_page_breaks + v_page_breaks})")
            
            if empty_formatted > total_with_content * 2:
                print(f"2. Use Find(xlValues) to detect actual data bounds")
                print(f"   UsedRange includes too many empty formatted cells")
            
            if ps.FitToPagesWide == 1 and ps.FitToPagesTall:
                print(f"3. Avoid FitToPages on both dimensions - causes table splitting")
                print(f"   Use Zoom scaling OR FitToPagesWide=1 with FitToPagesTall=False")
        
        workbook.Close(False)
        
    finally:
        excel.Quit()
    
    print(f"\n{'='*80}")
    print("Analysis Complete")
    print(f"{'='*80}\n")


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python analyze_excel.py <path-to-excel-file>")
        return 2

    file_path = Path(argv[1]).expanduser()
    if not file_path.exists():
        print(f"File not found: {file_path}")
        return 1

    analyze_excel_file(str(file_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
