---
name: word
description: Read, make or change Word documents (.docx) on this Mac, with Python's python-docx or with Microsoft Word through AppleScript, without losing their formatting.
---

# Word documents on this Mac

## Pick the way

- The file alone (read the text, change words, fill a table, add paragraphs): python-docx, run with Dot's own Python,
  `{{dot_python}}`. The `python3` of the shell may not have it; install nothing into Dot's Python. read_file shows the
  text of a document too.
- Word itself (a document open in Word, a PDF laid out as Word prints it, fields, a table of contents, tracked changes,
  an old .doc file): drive Microsoft Word with apple_script.

Before you change a document, look in its folder for a file whose name starts with `~$` and ends like the document's
(Word cuts the start of a long name): Word keeps it while the document is open, and changing the document under it loses
one of the two versions. Change it through Word then, or ask the user to close it.

## Changing text with python-docx

- A paragraph is a list of runs, and each run has its own formatting (bold, italic, font, colour). Setting
  `paragraph.text` replaces all of them with one plain run: change `run.text` instead.
- A phrase can be split over several runs (Word splits text where formatting, spelling marks or edits change). When the
  phrase is in `paragraph.text` but in no single run, put the new words in the first run the phrase touches and take the
  rest of the phrase out of the runs after it, so each part keeps the formatting it had.
- `document.paragraphs` holds only the body. Text is also in tables (`document.tables`, then `cell.paragraphs` and
  `cell.tables`), and in the headers and footers of each section (`section.header.paragraphs`,
  `section.footer.paragraphs` and their tables). python-docx does not reach text boxes, footnotes and comments: say so
  when the text may be there.
- Count what you changed, and read the document back: the old text is gone, the formatting is where it was.
- Save over the file only when the user asked to change it; otherwise write a new file next to it.

```sh
{{dot_python}} - <<'EOF'
from docx import Document
document = Document("/path/to/letter.docx")
for paragraph in document.paragraphs:
    for run in paragraph.runs:
        print(run.bold, run.italic, run.text)
EOF
```

## Word through AppleScript

Terms of Word's dictionary (`sdef "/Applications/Microsoft Word.app"` prints all of it):

- `open (POSIX file "/path/to/letter.docx")` opens a document; `active document` is the one in front.
- `execute find (find object of text object of active document) find text "old" replace with "new" replace replace all`
  changes every match in the body and keeps its formatting; the headers and footers are not in the body.
- `save as active document file name "/path/to/letter.pdf" file format format PDF` writes a PDF; `file format format
  document` writes a .docx, also of an old .doc.
- `close active document saving no` closes it without saving.

Word is sandboxed like Excel: the first time it opens a file outside its own folders it can show a Grant File Access
dialog, and the first script that drives it makes macOS ask the user whether Dot may control it (error -1743 when that
was refused; the user allows it in System Settings > Privacy & Security > Automation). Only the person at the Mac can
answer either: a script that times out may be waiting for them, so say what they have to click, or work on the file
with python-docx.
