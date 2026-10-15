// What the panel beside the conversation shows of a file (item 195): a file a message carries, or one the conversation
// names by its path. The daemon reads the file (server/routes_files.py) and this draws what it read with text nodes only:
// the pages of a PDF as pictures the daemon draws, an image, a workbook or a CSV table as a grid with a tab for each
// sheet, a document's headings, paragraphs, lists and tables, a presentation's slides, a Markdown file formatted with
// its source a click away (item 269), and any other text as text. A page without the panel shows a file in a dialog.

import { api, blobUrl, query } from "./api.js";
import { sizeLabel } from "./attachments.js";
import { el } from "./dom.js";
import { icon } from "./icons.js";
import { segmented } from "./panels.js";
import { prose } from "./prose.js";
import { toast } from "./ui.js";

const KINDS = {
  pdf: "PDF",
  image: "Image",
  document: "Word document",
  workbook: "Excel workbook",
  presentation: "Presentation",
  table: "Table",
  text: "Text",
  unshown: "File",
};
// The most cells a grid draws, the empty ones among them included.
const GRID_CELLS = 40_000;
// How far below the view the pages of a PDF are drawn before they come into it.
const PAGE_MARGIN = "800px 0px";
// A value a grid aligns to the right, as a spreadsheet does a number.
const NUMBER = /^[-+]?[\d\s.,']*\d%?$/;
// A file read as Markdown (item 269).
const MARKDOWN_NAME = /\.(md|markdown)$/i;

function baseName(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

export function columnLetters(number) {
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
  return `${cell.formula} → ${cell.value ?? "not worked out yet"}`;
}

/**
 * A sheet as a grid of the rows and the columns that hold anything, under their numbers and letters, drawn to at most
 * GRID_CELLS cells. `onPick(cell)` runs when a cell that holds anything is clicked.
 */
function sheetGrid(sheet, onPick) {
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
  const table = el(
    "table",
    { class: "file-grid" },
    el("thead", null, el("tr", null, el("th", { class: "corner" }), columns.map((column) => el("th", { scope: "col" }, columnLetters(column))))),
    el(
      "tbody",
      null,
      drawn.map((row) =>
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
        ),
      ),
    ),
  );
  table.addEventListener("click", (event) => {
    const target = event.target.closest("td[data-cell]");
    if (target !== null) {
      onPick(picked[Number(target.dataset.cell)]);
    }
  });
  const left = numbers.length - drawn.length;
  return [table, left > 0 ? hint(`The grid stops at row ${drawn[drawn.length - 1]}; ${left} more rows hold something. Open the file on the Mac to see them.`) : null];
}

/** The note under a sheet that was read in part. */
function cutNote(sheet, maxCells, noun) {
  if (sheet.cut_at_row === null) {
    return null;
  }
  return hint(`The ${noun} holds more than ${maxCells.toLocaleString()} cells; from row ${sheet.cut_at_row} on it is not shown here. Open it on the Mac to see it all.`);
}

function sheetView(sheet, maxCells, noun, line) {
  if (sheet.cells.length === 0 && sheet.cut_at_row === null) {
    return [hint("This sheet is empty.")];
  }
  const pick = (cell) => {
    line.hidden = false;
    line.replaceChildren(el("strong", null, cellAddress(cell)), el("span", null, cellContent(cell)));
  };
  return [...sheetGrid(sheet, pick), cutNote(sheet, maxCells, noun)];
}

function workbookView(preview, toolbar, body) {
  const line = el("div", { class: "file-cell-line", hidden: true, "aria-live": "polite" });
  const show = (index) => {
    line.hidden = true;
    const sheet = preview.sheets[index];
    body.replaceChildren(el("div", { class: "file-sheet" }, sheetView(sheet, preview.max_cells, "workbook", line)));
    body.scrollTo(0, 0);
  };
  const unread = preview.sheet_count - preview.sheets.length;
  const tabs =
    preview.sheets.length > 1
      ? segmented(
          preview.sheets.map((sheet, index) => ({
            id: index,
            label: sheet.state === "visible" ? sheet.title : `${sheet.title} (hidden)`,
            hint: `Show the sheet ${sheet.title}`,
          })),
          0,
          show,
        ).element
      : null;
  if (tabs !== null) {
    tabs.classList.add("file-tabs");
    tabs.setAttribute("aria-label", "Sheets");
  }
  fill(toolbar, tabs, unread > 0 ? el("p", { class: "small muted" }, `${unread} more sheets are not shown.`) : null, line);
  show(0);
}

function tableView(preview, toolbar, body) {
  const line = el("div", { class: "file-cell-line", hidden: true, "aria-live": "polite" });
  toolbar.replaceChildren(line);
  body.replaceChildren(el("div", { class: "file-sheet" }, sheetView(preview.sheet, preview.max_cells, "table", line)));
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
  return el("div", { class: "file-doc prose" }, nodes.length > 0 ? nodes : hint("The document holds no text."));
}

function chartView(chart) {
  const named = chart.title === "" ? "Chart" : `Chart “${chart.title}”`;
  if (chart.kinds === null) {
    return el("p", { class: "muted" }, `${named}: of a kind that is not read (3D bars or lines, stock or surface).`);
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
    return hint("The presentation has no slides.");
  }
  const count = preview.slides.length;
  return el(
    "div",
    { class: "file-slides" },
    preview.slides.map((slide, index) =>
      el(
        "section",
        { class: "file-slide prose", "aria-label": `Slide ${index + 1} of ${count}` },
        el("div", { class: "file-slide-number small muted" }, `Slide ${index + 1}`),
        slide.title === "" ? null : el("h3", null, slide.title),
        slide.parts.map(partView),
        slide.notes === "" ? null : el("div", { class: "file-slide-notes small" }, el("strong", null, "Notes"), el("p", { class: "para" }, slide.notes)),
      ),
    ),
  );
}

// ------------------------------------------------------------------------------------------------ text
/** A text as text; a Markdown file formatted as an answer is, its HTML never drawn, with its source a click away. */
function textView(file, toolbar, body) {
  const cut = file.preview.cut ? hint("The file is longer than this; open it on the Mac to read it all.") : null;
  const source = () => el("pre", { class: "file-text" }, file.preview.text);
  if (!MARKDOWN_NAME.test(file.name)) {
    fill(body, source(), cut);
    return;
  }
  const show = (id) => fill(body, id === "formatted" ? el("div", { class: "file-markdown" }, prose(file.preview.text)) : source(), cut);
  const views = segmented(
    [
      { id: "formatted", label: "Formatted", hint: "Show the Markdown formatted" },
      { id: "source", label: "Source", hint: "Show the Markdown as it is written" },
    ],
    "formatted",
    show,
  ).element;
  views.classList.add("file-tabs");
  views.setAttribute("aria-label", "View");
  fill(toolbar, views);
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
    return hint("The PDF has no pages.");
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
      (error) => figure.replaceChildren(note("warn", `Page ${number} could not be drawn: ${error.message}`)),
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
    const figure = el("figure", { class: "file-page", dataset: { page: String(index + 1) } }, el("img", { alt: `Page ${index + 1} of ${count}`, decoding: "async" }));
    figure.style.aspectRatio = `${page.width} / ${page.height}`;
    observer.observe(figure);
    return figure;
  });
  return el("div", { class: "file-pages" }, pages);
}

function imageView(file, keep) {
  const image = el("img", { class: "file-image", alt: file.name });
  const holder = el("div", { class: "file-picture" }, image);
  blobUrl(`/files/image${query({ path: file.path })}`).then(
    (url) => {
      if (keep(url) !== null) {
        image.src = url;
      }
    },
    (error) => holder.replaceChildren(note("warn", `The image could not be loaded: ${error.message}`)),
  );
  return holder;
}

// ------------------------------------------------------------------------------------------------ the view
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
  const facts = el("span", { class: "small muted" }, "Loading…");
  let path = null;
  const openButton = el(
    "button",
    {
      class: "btn sm",
      type: "button",
      hidden: true,
      title: "Open the file in its application on the Mac",
      onclick: () =>
        api.post("/files/open", { path }).then(
          () => toast("ok", `Opened ${title.textContent} on the Mac`),
          (error) => toast("error", `Could not open the file: ${error.message}`),
        ),
    },
    icon("external"),
    el("span", null, "Open on the Mac"),
  );
  const closeButton = el("button", { class: "btn ghost sm icon-only", type: "button", title: "Close", "aria-label": "Close the file", onclick: onClose }, icon("x"));
  const toolbar = el("div", { class: "file-toolbar" });
  const body = el("div", { class: "file-body" }, hint("Loading…"));
  const element = el(
    "section",
    { class: "file-view", tabindex: "-1", "aria-label": label },
    el("div", { class: "file-head" }, icon("file"), el("div", { class: "file-heading" }, title, facts), openButton, closeButton),
    toolbar,
    body,
  );

  function paint(file) {
    const kind = file.preview.kind;
    path = file.path;
    title.textContent = file.name;
    title.title = file.path;
    element.setAttribute("aria-label", file.name);
    element.dataset.kind = kind;
    facts.textContent = `${KINDS[kind]}, ${sizeLabel(file.size)}`;
    openButton.hidden = !file.opens;
    switch (kind) {
      case "pdf":
        body.replaceChildren(pdfView(file, file.preview, body, keep, cleanups));
        break;
      case "image":
        body.replaceChildren(imageView(file, keep));
        break;
      case "workbook":
        workbookView(file.preview, toolbar, body);
        break;
      case "table":
        tableView(file.preview, toolbar, body);
        break;
      case "document":
        body.replaceChildren(documentView(file.preview));
        break;
      case "presentation":
        body.replaceChildren(presentationView(file.preview));
        break;
      case "text":
        textView(file, toolbar, body);
        break;
      default:
        fill(body, note(null, `This file is not shown here: ${file.preview.reason}.`), file.opens ? hint("Open it on the Mac to see it.") : null);
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
        body.replaceChildren(note("warn", `This file cannot be shown: ${error.message}.`));
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
