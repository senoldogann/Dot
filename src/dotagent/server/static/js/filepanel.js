// What the panel beside the conversation shows of a file (item 195): a file a message carries, or one the conversation
// names by its path. The daemon reads the file (server/routes_files.py) and this draws what it read with text nodes only:
// the pages of a PDF as pictures the daemon draws, an image, a workbook or a CSV table as a grid with a tab for each
// sheet, a document's headings, paragraphs, lists and tables, a presentation's slides, a Markdown file formatted with
// its source a click away (item 269), and any other text as text. A Word, Excel or PowerPoint file shows first as
// Quick Look draws it (item 268), in a sandboxed frame, with what the panel reads of it a click away. A page without
// the panel shows a file in a dialog.

import { api, blobUrl, query } from "./api.js";
import { sizeLabel } from "./attachments.js";
import { copyButton } from "./copybutton.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { segmented } from "./panels.js";
import { codeView, fileLines } from "./codeview.js";
import { changeFormat, prettyJSON } from "./diffview.js";
import { appendInPages } from "./lazylist.js";
import { parseBlocks } from "./markdown.js";
import { blockNode } from "./prose.js";
import { showImage, toast } from "./ui.js";
import { t } from "./i18n.js";

const KINDS = {
  pdf: "PDF",
  image: t("filepanel.image"),
  document: t("filepanel.wordDocument"),
  workbook: t("filepanel.excelWorkbook"),
  presentation: t("filepanel.presentation"),
  table: t("filepanel.table"),
  text: t("filepanel.text"),
  unshown: t("filepanel.file"),
};
// The most cells a grid draws, the empty ones among them included.
const GRID_CELLS = 40_000;
// How many rows of a grid are drawn at a time, the next as the reader scrolls to them.
const GRID_ROWS_AT_A_TIME = 200;
// How many blocks of a Markdown file are drawn at a time (the owner's report of 2026-11-28: a report of 200 KB).
const MARKDOWN_BLOCKS_AT_A_TIME = 120;
// How far below the view the pages of a PDF are drawn before they come into it.
const PAGE_MARGIN = "800px 0px";
// A value a grid aligns to the right, as a spreadsheet does a number.
const NUMBER = /^[-+]?[\d\s.,']*\d%?$/;
// A file read as Markdown (item 269).
const MARKDOWN_NAME = /\.(md|markdown)$/i;

function baseName(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

function columnLetters(number) {
  let letters = "";
  let rest = number;
  while (rest > 0) {
    letters = String.fromCharCode(65 + ((rest - 1) % 26)) + letters;
    rest = Math.floor((rest - 1) / 26);
  }
  return letters;
}

/** A note in the panel: `warn` for what went wrong, `null` for what the panel does not show. */
function note(kind, text) {
  return el("div", { class: kind === null ? "note" : `note ${kind}` }, icon("alert"), el("span", null, text));
}

function hint(text) {
  return el("p", { class: "hint" }, text);
}

/** Put `children` in `node` in place of what it held, leaving out the nulls that `replaceChildren` would write out. */
function fill(node, ...children) {
  node.replaceChildren(...children.filter((child) => child !== null));
}

// ------------------------------------------------------------------------------------------------ tables and grids
function officeTable(rows) {
  const width = Math.max(...rows.map((row) => row.length));
  const filled = rows.map((row) => [...row, ...Array(width - row.length).fill("")]);
  const [head, ...body] = filled;
  return el(
    "table",
    { class: "file-table" },
    el("thead", null, el("tr", null, head.map((cell) => el("th", null, cell)))),
    el("tbody", null, body.map((row) => el("tr", null, row.map((cell) => el("td", null, cell))))),
  );
}

function cellClass(cell) {
  const classes = [];
  if (cell.formula !== null) {
    classes.push("formula");
  }
  if (cell.value === null) {
    classes.push("pending");
  } else if (NUMBER.test(cell.value)) {
    classes.push("num");
  }
  return classes.join(" ");
}

function cellAddress(cell) {
  return `${columnLetters(cell.column)}${cell.row}`;
}

/** What a cell holds, as its line above the grid says it. */
function cellContent(cell) {
  if (cell.formula === null) {
    return cell.value;
  }
  return `${cell.formula} → ${cell.value ?? t("filepanel.notWorkedOutYet")}`;
}

/**
 * A sheet as a grid of the rows and the columns that hold anything, under their numbers and letters, drawn to at most
 * GRID_CELLS cells, `GRID_ROWS_AT_A_TIME` rows at a time as `root` scrolls to them. `onPick(cell)` runs when a cell that
 * holds anything is clicked.
 */
function sheetGrid(sheet, onPick, root) {
  const columns = [...new Set(sheet.cells.map((cell) => cell.column))].sort((a, b) => a - b);
  const rows = new Map();
  for (const cell of sheet.cells) {
    if (!rows.has(cell.row)) {
      rows.set(cell.row, new Map());
    }
    rows.get(cell.row).set(cell.column, cell);
  }
  const numbers = [...rows.keys()];
  const drawn = numbers.slice(0, Math.max(1, Math.floor(GRID_CELLS / Math.max(columns.length, 1))));
  const picked = [];
  const rowNode = (row) =>
    el(
      "tr",
      null,
      el("th", { scope: "row" }, String(row)),
      columns.map((column) => {
        const cell = rows.get(row).get(column);
        if (cell === undefined) {
          return el("td");
        }
        picked.push(cell);
        return el("td", { class: cellClass(cell), title: cell.formula ?? undefined, dataset: { cell: String(picked.length - 1) } }, cell.value ?? cell.formula);
      }),
    );
  const tbody = el("tbody");
  const table = el(
    "table",
    { class: "file-grid" },
    el("thead", null, el("tr", null, el("th", { class: "corner" }), columns.map((column) => el("th", { scope: "col" }, columnLetters(column))))),
    tbody,
  );
  appendInPages(tbody, drawn.length, GRID_ROWS_AT_A_TIME, (index) => rowNode(drawn[index]), root);
  table.addEventListener("click", (event) => {
    const target = event.target.closest("td[data-cell]");
    if (target !== null) {
      onPick(picked[Number(target.dataset.cell)]);
    }
  });
  const left = numbers.length - drawn.length;
  return [table, left > 0 ? hint(t("filepanel.theGridStopsAtRow", { value: drawn[drawn.length - 1], left })) : null];
}

/** The note under a sheet that was read in part. */
function cutNote(sheet, maxCells, noun) {
  if (sheet.cut_at_row === null) {
    return null;
  }
  return hint(t("filepanel.theNounHoldsMoreThan", { noun, toLocaleString: maxCells.toLocaleString(), cut_at_row: sheet.cut_at_row }));
}

function sheetView(sheet, maxCells, noun, line, root) {
  if (sheet.cells.length === 0 && sheet.cut_at_row === null) {
    return [hint(t("filepanel.thisSheetIsEmpty"))];
  }
  const pick = (cell) => {
    line.hidden = false;
    line.replaceChildren(el("strong", null, cellAddress(cell)), el("span", null, cellContent(cell)));
  };
  return [...sheetGrid(sheet, pick, root), cutNote(sheet, maxCells, noun)];
}

function workbookView(preview, toolbar, body) {
  const line = el("div", { class: "file-cell-line", hidden: true, "aria-live": "polite" });
  const show = (index) => {
    line.hidden = true;
    const sheet = preview.sheets[index];
    body.replaceChildren(el("div", { class: "file-sheet" }, sheetView(sheet, preview.max_cells, "workbook", line, body)));
    body.scrollTo(0, 0);
  };
  const unread = preview.sheet_count - preview.sheets.length;
  const tabs =
    preview.sheets.length > 1
      ? segmented(
          preview.sheets.map((sheet, index) => ({
            id: index,
            label: sheet.state === "visible" ? sheet.title : t("filepanel.titleHidden", { title: sheet.title }),
            hint: t("filepanel.showTheSheetTitle", { title: sheet.title }),
          })),
          0,
          show,
        ).element
      : null;
  if (tabs !== null) {
    tabs.classList.add("file-tabs");
    tabs.setAttribute("aria-label", t("filepanel.sheets"));
  }
  fill(toolbar, tabs, unread > 0 ? el("p", { class: "small muted" }, t("filepanel.unreadMoreSheetsAreNot", { unread })) : null, line);
  show(0);
}

function tableView(preview, toolbar, body) {
  const line = el("div", { class: "file-cell-line", hidden: true, "aria-live": "polite" });
  toolbar.replaceChildren(line);
  body.replaceChildren(el("div", { class: "file-sheet" }, sheetView(preview.sheet, preview.max_cells, "table", line, body)));
}

// ------------------------------------------------------------------------------------------------ documents and slides
function documentView(preview) {
  const nodes = [];
  let list = null;
  for (const block of preview.blocks) {
    if (block.kind === "paragraph" && block.listed) {
      if (list === null) {
        list = el("ul");
        nodes.push(list);
      }
      list.append(el("li", null, block.text));
      continue;
    }
    list = null;
    if (block.kind === "table") {
      nodes.push(officeTable(block.rows));
    } else if (block.heading !== null) {
      nodes.push(el(`h${Math.min(block.heading + 1, 4)}`, null, block.text));
    } else {
      nodes.push(el("p", { class: "para" }, block.text));
    }
  }
  return el("div", { class: "file-doc prose" }, nodes.length > 0 ? nodes : hint(t("filepanel.theDocumentHoldsNoText")));
}

function chartView(chart) {
  const named = chart.title === "" ? t("filepanel.chart") : t("filepanel.chartTitle", { title: chart.title });
  if (chart.kinds === null) {
    return el("p", { class: "muted" }, t("filepanel.namedOfAKindThat", { named }));
  }
  const longest = chart.series.reduce((most, series) => (series.points.length > most.points.length ? series : most), { points: [] });
  const categories = longest.points.map((point, index) => point.category ?? String(index + 1));
  return el(
    "figure",
    { class: "file-chart" },
    el("figcaption", { class: "small muted" }, chart.kinds.length > 0 ? `${named}, ${chart.kinds.join(", ")}` : named),
    officeTable([["", ...categories], ...chart.series.map((series) => [series.name, ...series.points.map((point) => point.value ?? "")])]),
  );
}

function partView(part) {
  switch (part.kind) {
    case "text":
      return part.lines.map((line) => el("p", { class: `file-line level-${Math.min(line.level, 4)}` }, line.text));
    case "table":
      return officeTable(part.rows);
    default:
      return chartView(part);
  }
}

function presentationView(preview) {
  if (preview.slides.length === 0) {
    return hint(t("filepanel.thePresentationHasNoSlides"));
  }
  const count = preview.slides.length;
  return el(
    "div",
    { class: "file-slides" },
    preview.slides.map((slide, index) =>
      el(
        "section",
        { class: "file-slide prose", "aria-label": t("filepanel.slideValueOfCount", { value: index + 1, count }) },
        el("div", { class: "file-slide-number small muted" }, t("filepanel.slideValue", { value: index + 1 })),
        slide.title === "" ? null : el("h3", null, slide.title),
        slide.parts.map(partView),
        slide.notes === "" ? null : el("div", { class: "file-slide-notes small" }, el("strong", null, t("filepanel.notes")), el("p", { class: "para" }, slide.notes)),
      ),
    ),
  );
}

/** What the panel reads of an Office file: a workbook's sheets, a document's text, a presentation's slides. */
function contentsView(preview, toolbar, body) {
  switch (preview.kind) {
    case "workbook":
      workbookView(preview, toolbar, body);
      return;
    case "document":
      fill(toolbar);
      body.replaceChildren(documentView(preview));
      return;
    default:
      fill(toolbar);
      body.replaceChildren(presentationView(preview));
  }
}

/**
 * An Office file as Quick Look draws it (item 268): its fonts, fills and pictures, a workbook's sheets one tab each, in
 * a frame that runs Quick Look's scripts sandboxed. `lookAddress` is where the daemon draws it, once; what the panel
 * reads of the file is a click away (`views`), and is what shows when Quick Look cannot draw it.
 */
function officeView(file, lookAddress, views, toolbar, body) {
  let drawing = null;
  let chosen = "drawn";
  let turns = 0;
  const failed = (error) => {
    switcher.set("contents");
    chosen = "contents";
    body.dataset.view = "contents";
    contentsView(file.preview, toolbar, body);
    toolbar.prepend(note("warn", t("filepanel.quickLookCouldNotDraw", { message: error.message })));
  };
  const show = (id) => {
    chosen = id;
    body.dataset.view = id;
    if (id === "contents") {
      contentsView(file.preview, toolbar, body);
      return;
    }
    fill(toolbar);
    fill(body, hint(t("filepanel.quickLookIsDrawingThe")));
    drawing ??= api.get(lookAddress);
    turns += 1;
    const turn = turns;
    drawing.then(
      (look) => {
        if (chosen === "drawn" && turn === turns) {
          drawnPages(look.pages);
        }
      },
      (error) => {
        if (chosen === "drawn" && turn === turns) {
          failed(error);
        }
      },
    );
  };
  // A drawing's pages: one frame, and a workbook's sheets as tabs over it, each sheet framed as it is (a frame inside
  // the sandboxed page of Quick Look's own tabs would be refused by its parts).
  const drawnPages = (pages) => {
    const frame = el("iframe", { class: "file-look", sandbox: "allow-scripts", referrerpolicy: "no-referrer" });
    const open = (page) => {
      frame.src = page.url;
      frame.title = page.name === "" ? t("filepanel.nameAsQuickLookDraws", { name: file.name }) : t("filepanel.nameASheetOfName2", { name: page.name, name2: file.name });
    };
    fill(toolbar);
    if (pages.length > 1) {
      const sheets = segmented(
        pages.map((page, index) => ({ id: String(index), label: page.name, hint: t("filepanel.theSheetName", { name: page.name }) })),
        "0",
        (id) => open(pages[Number(id)]),
      );
      sheets.element.classList.add("file-tabs");
      sheets.element.setAttribute("aria-label", t("filepanel.sheets"));
      fill(toolbar, sheets.element);
    }
    open(pages[0]);
    fill(body, frame);
  };
  const switcher = segmented(
    [
      { id: "drawn", label: t("filepanel.asDrawn"), hint: t("filepanel.showTheFileAsQuick") },
      { id: "contents", label: t("filepanel.contents"), hint: t("filepanel.showTheTextAndValues") },
    ],
    "drawn",
    show,
  );
  switcher.element.classList.add("file-tabs");
  switcher.element.setAttribute("aria-label", t("filepanel.view"));
  fill(views, switcher.element);
  views.hidden = false;
  show("drawn");
}

// ------------------------------------------------------------------------------------------------ text
// A source file, by its extension, reads as code (item 624), drawn by the change view's code view (item 2017): its
// lines numbered, its words coloured by diffview.js's scanner, a JSON file laid out.
const CODE_FILE = /\.(?:py|pyi|js|mjs|cjs|jsx|ts|tsx|mts|cts|json|jsonl|webmanifest|css|scss|html|xml|sh|bash|zsh|rs|go|java|kt|kts|c|h|cc|cpp|hpp|cs|php|rb|sql|toml|yaml|yml|ini|swift|dart|vue|svelte|plist|svg)$/i;

function codeFileView(file, toolbar, body, cut) {
  const format = changeFormat(file.name);
  const language = format.kind === "code" ? format.language : format.kind;
  const extension = file.name.match(/\.([^.]+)$/)?.[1]?.toLowerCase() ?? "";
  const path = file.path ?? file.name;
  const breadcrumb = path.split(/[\\/]/).filter(Boolean).join(" / ");
  fill(
    toolbar,
    el("div", { class: "file-code-path", title: path },
      el("span", { class: "file-code-language" }, language ?? extension),
      el("span", { class: "file-code-breadcrumb" }, breadcrumb),
    ),
  );
  const text = format.kind === "json" ? (prettyJSON(file.preview.text) ?? file.preview.text) : file.preview.text;
  const view = codeView(fileLines(text.replace(/\r\n?/g, "\n")), language, false);
  view.setAttribute("aria-label", t("filepanel.languageSourceCode", { language: language ?? extension }));
  fill(body, view, cut);
}

/** A Markdown file formatted as an answer is, its blocks drawn `MARKDOWN_BLOCKS_AT_A_TIME` at a time as `root`
 * scrolls to them. */
function markdownBody(text, root) {
  const blocks = parseBlocks(text);
  const holder = el("div", { class: "prose" });
  appendInPages(holder, blocks.length, MARKDOWN_BLOCKS_AT_A_TIME, (index) => blockNode(blocks[index]), root);
  return el("div", { class: "file-markdown" }, holder);
}

// A web page, by its extension (item 1077).
const WEB_PAGE_NAME = /\.html?$/i;

/** A web page as a browser draws it (item 1077), without its scripts: the frame is sandboxed with nothing allowed, so
 * no script of the page runs (one could take the frame to another site, which a frame's sandbox allows) and no
 * refresh moves it; it is served as Quick Look's drawings are, so it reaches no server, nor the pictures and styles
 * beside it on the disk. Its source is a click away. */
function webPageView(file, views, toolbar, body, cut) {
  let page = null;
  let chosen = "drawn";
  const show = (id) => {
    chosen = id;
    // Drawn, the page fills the panel as a drawing of Quick Look does.
    body.dataset.view = id;
    if (id === "source") {
      codeFileView(file, toolbar, body, cut);
      return;
    }
    fill(toolbar);
    fill(body, hint(t("filepanel.loadingThePage")));
    page ??= api.get(`/files/web-page${query({ path: file.path })}`);
    page.then(
      (look) => {
        if (chosen === "drawn") {
          const frame = el("iframe", { class: "file-look", sandbox: "", referrerpolicy: "no-referrer", title: t("filepanel.nameAsAPage", { name: file.name }) });
          frame.src = look.pages[0].url;
          fill(body, frame);
        }
      },
      (error) => {
        if (chosen === "drawn") {
          fill(body, note("warn", t("filepanel.thePageCannotBeShown", { message: error.message })));
        }
      },
    );
  };
  const switcher = segmented(
    [
      { id: "drawn", label: t("filepanel.page"), hint: t("filepanel.showThePageAsA") },
      { id: "source", label: t("filepanel.source"), hint: t("filepanel.showThePageSHtml") },
    ],
    "drawn",
    show,
  );
  switcher.element.classList.add("file-tabs");
  switcher.element.setAttribute("aria-label", t("filepanel.view"));
  fill(views, switcher.element);
  views.hidden = false;
  show("drawn");
}

/** A text as text; a Markdown file formatted as an answer is, its HTML never drawn, with its source a click away; a
 * web page drawn as one (item 1077); a source file as code. */
function textView(file, views, toolbar, body) {
  const cut = file.preview.cut ? hint(t("filepanel.theFileIsLongerThan")) : null;
  if (WEB_PAGE_NAME.test(file.name)) {
    webPageView(file, views, toolbar, body, cut);
    return;
  }
  if (CODE_FILE.test(file.name)) {
    codeFileView(file, toolbar, body, cut);
    return;
  }
  const source = () => el("pre", { class: "file-text" }, file.preview.text);
  if (!MARKDOWN_NAME.test(file.name)) {
    fill(body, source(), cut);
    return;
  }
  const show = (id) => fill(body, id === "formatted" ? markdownBody(file.preview.text, body) : codeView(fileLines(file.preview.text), "markdown", false), cut);
  const tabs = segmented(
    [
      { id: "formatted", label: t("filepanel.formatted"), hint: t("filepanel.showTheMarkdownFormatted") },
      { id: "source", label: t("filepanel.source"), hint: t("filepanel.showTheMarkdownAsIt") },
    ],
    "formatted",
    show,
  ).element;
  tabs.classList.add("file-tabs");
  tabs.setAttribute("aria-label", t("filepanel.view"));
  fill(toolbar, tabs);
  show("formatted");
}

// ------------------------------------------------------------------------------------------------ pictures
/** The smallest of the widths a page is drawn at that fills `pixels`, or the largest. */
function pageWidth(widths, pixels) {
  return widths.find((width) => width >= pixels) ?? widths[widths.length - 1];
}

/**
 * The pages of a PDF, each drawn by the daemon once it comes near the view of `scroller`. `keep(url)` holds a page's
 * picture for the view, or lets it go and answers null once the view is gone.
 */
function pdfView(file, preview, scroller, keep, cleanups) {
  if (preview.pages.length === 0) {
    return hint(t("filepanel.thePdfHasNoPages"));
  }
  const count = preview.pages.length;
  const draw = (figure) => {
    const number = Number(figure.dataset.page);
    const width = pageWidth(preview.widths, figure.clientWidth * window.devicePixelRatio);
    blobUrl(`/files/page${query({ path: file.path, number, width })}`).then(
      (url) => {
        if (keep(url) !== null) {
          figure.querySelector("img").src = url;
          figure.dataset.drawn = "true";
        }
      },
      (error) => figure.replaceChildren(note("warn", t("filepanel.pageNumberCouldNotBe", { number, message: error.message }))),
    );
  };
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          observer.unobserve(entry.target);
          draw(entry.target);
        }
      }
    },
    { root: scroller, rootMargin: PAGE_MARGIN },
  );
  cleanups.push(() => observer.disconnect());
  const pages = preview.pages.map((page, index) => {
    const figure = el("figure", { class: "file-page", dataset: { page: String(index + 1) } }, el("img", { alt: t("filepanel.pageValueOfCount", { value: index + 1, count }), decoding: "async" }));
    figure.style.aspectRatio = `${page.width} / ${page.height}`;
    observer.observe(figure);
    return figure;
  });
  return el("div", { class: "file-pages" }, pages);
}

/** A picture, drawn whole in the panel, which shows it at full size over the page when it is pressed, as the
 * screenshots of a run do. */
function imageView(file, keep) {
  const image = el("img", { class: "file-image", alt: "", decoding: "async" });
  const holder = el(
    "button",
    {
      class: "file-picture",
      type: "button",
      title: t("filepanel.showThePictureAtFull"),
      "aria-label": t("filepanel.nameShowAtFullSize", { name: file.name }),
    },
    image,
  );
  blobUrl(`/files/image${query({ path: file.path })}`).then(
    (url) => {
      if (keep(url) !== null) {
        image.src = url;
        holder.addEventListener("click", () => showImage(url, file.name));
      }
    },
    (error) => {
      holder.disabled = true;
      holder.removeAttribute("aria-label");
      holder.removeAttribute("title");
      holder.replaceChildren(note("warn", t("filepanel.theImageCouldNotBe", { message: error.message })));
    },
  );
  return holder;
}

// ------------------------------------------------------------------------------------------------ the view
/** The rows of an Office table as lines of cells set apart by tabs. */
function tabbed(rows) {
  return rows.map((row) => row.join("\t")).join("\n");
}

/** The text a copy of the file takes, "" when the panel reads none of it. */
function textIn(preview) {
  switch (preview.kind) {
    case "text":
    case "pdf":
      return preview.text;
    case "document":
      return preview.blocks
        .map((block) => (block.kind === "table" ? tabbed(block.rows) : block.listed ? `- ${block.text}` : block.text))
        .join("\n\n");
    case "presentation":
      return preview.slides.length === 0
        ? ""
        : preview.slides
            .map((slide, index) =>
              [
                slide.title === "" ? t("filepanel.slideNumber", { value: index + 1 }) : t("filepanel.slideNumberTitled", { value: index + 1, title: slide.title }),
                ...slide.parts.map((part) => (part.kind === "text" ? part.lines.map((line) => line.text).join("\n") : part.kind === "table" ? tabbed(part.rows) : "")),
                slide.notes === "" ? "" : t("filepanel.notesNotes", { notes: slide.notes }),
              ]
                .filter((text) => text !== "")
                .join("\n"),
            )
            .join("\n\n");
    default:
      return "";
  }
}

/** What a copy of the file takes: the text the panel reads of it, or null when it reads none. */
function textOf(preview) {
  const text = textIn(preview);
  return text.trim() === "" ? null : text;
}

/**
 * The view of the file `source` names: `{ attachment, name }` for a file a message carries, `{ path }` for one named by
 * its path. `onClose()` runs on its Close button. Returns `element` and `dispose()`, which lets go of its pictures.
 */
export function fileView(source, onClose) {
  const urls = [];
  const cleanups = [];
  let disposed = false;
  const keep = (url) => {
    if (disposed) {
      URL.revokeObjectURL(url);
      return null;
    }
    urls.push(url);
    return url;
  };
  const label = source.name ?? baseName(source.path);
  const title = el("strong", { class: "file-name" }, label);
  const facts = el("span", { class: "small muted" }, t("filepanel.loading"));
  let path = null;
  const openButton = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      hidden: true,
      title: t("filepanel.openTheFileInIts"),
      onclick: () =>
        api.post("/files/open", { path }).then(
          () => toast("ok", t("filepanel.openedTextcontentOnTheMac", { textContent: title.textContent })),
          (error) => toast("error", t("filepanel.couldNotOpenTheFile", { message: error.message })),
        ),
    },
    icon("external"),
    el("span", null, t("filepanel.openOnTheMac")),
  );
  const closeButton = el("button", { class: "btn ghost sm icon-only", type: "button", title: t("filepanel.close"), "aria-label": t("filepanel.closeTheFile"), onclick: onClose }, icon("x"));
  // The text of the file, a press away (item 403): what the panel reads of a text, a Markdown file, a PDF, a Word
  // document or a presentation; a workbook, a picture or a file not read here have none.
  let textOfFile = null;
  const copy = copyButton(() => textOfFile ?? "", t("filepanel.copyTheText"));
  copy.hidden = true;
  const views = el("div", { class: "file-views", hidden: true });
  const toolbar = el("div", { class: "file-toolbar" });
  const body = el("div", { class: "file-body" }, hint(t("filepanel.loading")));
  const element = el(
    "section",
    { class: "file-view", tabindex: "-1", "aria-label": label },
    el("div", { class: "file-head" }, icon("file"), el("div", { class: "file-heading" }, title, facts), copy, openButton, closeButton),
    views,
    toolbar,
    body,
  );
  const lookAddress = source.attachment === undefined ? `/files/look${query({ path: source.path })}` : `/attachments/${encodeURIComponent(source.attachment)}/look`;

  function paint(file) {
    const kind = file.preview.kind;
    path = file.path;
    title.textContent = file.name;
    title.title = file.path;
    element.setAttribute("aria-label", file.name);
    element.dataset.kind = kind;
    facts.textContent = `${kind === "text" && WEB_PAGE_NAME.test(file.name) ? t("filepanel.webPage") : KINDS[kind]}, ${sizeLabel(file.size)}`;
    openButton.hidden = !file.opens;
    textOfFile = textOf(file.preview);
    copy.hidden = textOfFile === null;
    copy.title = file.preview.cut ? t("filepanel.copyTheTextShownThe") : t("filepanel.copyTheText");
    switch (kind) {
      case "pdf":
        body.replaceChildren(pdfView(file, file.preview, body, keep, cleanups));
        break;
      case "image":
        body.replaceChildren(imageView(file, keep));
        break;
      case "workbook":
      case "document":
      case "presentation":
        officeView(file, lookAddress, views, toolbar, body);
        break;
      case "table":
        tableView(file.preview, toolbar, body);
        break;
      case "text":
        textView(file, views, toolbar, body);
        break;
      default:
        fill(body, note(null, t("filepanel.thisFileIsNotShown", { reason: file.preview.reason })), file.opens ? hint(t("filepanel.openItOnTheMac")) : null);
    }
  }

  const address = source.attachment === undefined ? `/files/preview${query({ path: source.path })}` : `/attachments/${encodeURIComponent(source.attachment)}/preview`;
  api.get(address).then(
    (file) => {
      if (!disposed) {
        paint(file);
      }
    },
    (error) => {
      if (!disposed) {
        facts.textContent = source.path ?? "";
        body.replaceChildren(note("warn", t("filepanel.thisFileCannotBeShown", { message: error.message })));
      }
    },
  );

  return {
    element,
    dispose: () => {
      disposed = true;
      for (const cleanup of cleanups) {
        cleanup();
      }
      for (const url of urls) {
        URL.revokeObjectURL(url);
      }
    },
  };
}

/**
 * Show the file `source` names in a dialog (item 269), on a page without the panel beside the conversation: the home's
 * box, an agent's. Escape, a click beside it or its Close button closes it, and `opener` gets the focus back.
 */
export function fileDialog(source, opener) {
  const dialog = el("dialog", { class: "modal file-dialog" });
  const view = fileView(source, () => dialog.close());
  dialog.append(view.element);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
  dialog.addEventListener("close", () => {
    view.dispose();
    dialog.remove();
    opener.focus({ preventScroll: true });
  });
  document.body.append(dialog);
  dialog.showModal();
  view.element.focus({ preventScroll: true });
}
