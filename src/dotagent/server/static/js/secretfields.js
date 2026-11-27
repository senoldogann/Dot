// A card of several secrets (item 2040): the fields it asks for, with the daemon's limits, and whether every one has a
// value the card may hold. Pure: approvals.js draws the card with them, and the tests read them without a page.

// The shortest secret a card takes when the daemon names none: one shorter would stand bare where a command prints it.
const SECRET_LEAST_CHARS = 8;

/**
 * The fields a card of secrets asks for (item 2040), with their limits from the daemon: each `{name, label, kind,
 * least, most}`; `[]` for a card of one value, as cards were before.
 */
export function secretFields(args) {
  if (!Array.isArray(args.fields) || args.fields.length < 2) {
    return [];
  }
  return args.fields.map((field) => ({
    name: String(field.name),
    label: String(field.label),
    kind: field.kind === "text" || field.kind === "file" ? field.kind : "secret",
    least: typeof field.min_chars === "number" ? field.min_chars : SECRET_LEAST_CHARS,
    most: typeof field.max_chars === "number" ? field.max_chars : 8192,
  }));
}

/** Whether every field has a value the card may hold: at least its least and at most its most (item 2040). */
export function secretFieldsReady(fields, values) {
  return fields.every((field) => {
    const typed = (values.get(field.name) ?? "").trim().length;
    return typed >= field.least && typed <= field.most;
  });
}
