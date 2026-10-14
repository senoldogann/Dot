---
name: excel
description: Read, make or change Excel workbooks (.xlsx, .xlsm) on this Mac, with Python's openpyxl or with Microsoft Excel through AppleScript, and get the results of their formulas right.
---

# Excel workbooks on this Mac

## Pick the way

- The file alone (read cells, add rows, write formulas, set formats): openpyxl, run with Dot's own Python,
  `{{dot_python}}`. It has openpyxl, python-docx and python-pptx; the `python3` of the shell may have none of them.
  Install nothing into Dot's Python. This way needs no application and works while Excel is closed.
- The results of formulas (a total the user asks for, a check that a sheet adds up): openpyxl computes no formula. Work
  the figure out in Python from the cells the formula reads, or have LibreOffice or Excel compute the workbook (below),
  and say which you did.
- Excel itself (a workbook open in Excel, macros, refreshing a pivot table or a data connection, a PDF laid out as Excel
  prints it): drive Microsoft Excel with apple_script.

Before you change a workbook, look in its folder for a file named `~$` and the workbook's name: Excel keeps it while
the workbook is open, and changing the workbook under it loses one of the two versions. Change it through Excel then, or
ask the user to close it.

## openpyxl

```sh
{{dot_python}} - <<'EOF'
from openpyxl import load_workbook
wb = load_workbook("/path/to/book.xlsx")  # formulas as text: "=SUM(B2:B9)"
results = load_workbook("/path/to/book.xlsx", data_only=True)  # the results the file holds, to read only
EOF
```

- `data_only=True` gives the results that the application that last saved the file stored in it: `None` for a formula
  openpyxl wrote, and an old figure when a cell the formula reads changed since. Never save a workbook loaded with
  `data_only=True`: every formula in it would become its last result.
- Write formulas in English with commas between the arguments, as the file stores them
  (`=SUMIF(A2:A9,"North",C2:C9)`), whatever language Excel shows.
- A workbook openpyxl saved holds no results, and Excel computes every formula when it opens it, so the user sees the
  right figures there.
- openpyxl drops drawn shapes, and charts and pictures may not come back as they were: change such a workbook through
  Excel, or say what may have changed.
- An .xlsm keeps its macros when it is loaded with `keep_vba=True` and saved as .xlsm again.
- Save over the file only when the user asked to change it; otherwise write a new file next to it.

## Results through LibreOffice

When `/Applications/LibreOffice.app` exists, it computes the formulas of a copy without opening a window. Give it a
profile of its own, so that it never meets the user's:

```sh
out=$(mktemp -d)
/Applications/LibreOffice.app/Contents/MacOS/soffice -env:UserInstallation=file://$(mktemp -d) --headless --norestore --convert-to xlsx --outdir "$out" /path/to/book.xlsx
```

It computes every formula whose result the file does not hold, which is every formula of a workbook openpyxl saved.
Read the results from the copy in `$out` with `data_only=True`, and check that the cells you need hold numbers, not
`None`. Leave the user's workbook as it is: LibreOffice's copy can lose some of Excel's formatting. Its first start can
wait on macOS asking the user to confirm opening LibreOffice, which only the person at the Mac can answer.

## Excel through AppleScript

Terms of Excel's dictionary (`sdef "/Applications/Microsoft Excel.app"` prints all of it):

- `open workbook workbook file name "/path/to/book.xlsx"` opens a workbook and returns it; `active workbook` is the one
  in front, and `name of every workbook` lists the open ones.
- `calculate full rebuild` computes every formula of the open workbooks.
- `value of range "B10" of worksheet "Summary" of wb` is the result of a cell, `formula of range "B10" of ...` its
  formula.
- `save wb` saves a workbook; `save workbook as wb filename "/path/to/book.pdf" file format PDF file format` writes a
  PDF of it; `close wb saving no` closes it.

Excel is sandboxed: the first time it opens a file outside its own folders it can show a Grant File Access dialog, and
the script waits for the answer. The first script that drives Excel makes macOS ask the user whether Dot may control it;
error -1743 means that was refused, and the user allows it in System Settings > Privacy & Security > Automation. Only the
person at the Mac can answer either: a script that times out may be waiting for them, so say what they have to click, or
work on the file with openpyxl.
