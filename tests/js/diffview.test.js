// A changed file read for its view (diffview.js): the same rules as the phone's (DotEngine's ChangeViewTests).

import assert from "node:assert/strict";
import { test } from "node:test";

import { changeFormat, changeLayout, diffRows, highlightLines, isNewFile, prettyJSON } from "../../src/dotagent/server/static/js/diffview.js";

/** A file of `GET /runs/{id}/changes`. */
function change(path, diff, { created = false, gone = false, note = null } = {}) {
  return { path, created, gone, diff, note };
}

test("rows carry both line numbers and drop the sign", () => {
  const diff = "--- a\n+++ b\n@@ -10,3 +10,4 @@ def run():\n     keep = 1\n-    old = 2\n+    new = 2\n+    more = 3\n     end = 4\n\\ No newline at end of file\n";
  assert.deepEqual(diffRows(diff), [
    { kind: "hunk", old: 10, new: 10, text: "def run():" },
    { kind: "context", old: 10, new: 10, text: "    keep = 1" },
    { kind: "removed", old: 11, new: null, text: "    old = 2" },
    { kind: "added", old: null, new: 11, text: "    new = 2" },
    { kind: "added", old: null, new: 12, text: "    more = 3" },
    { kind: "context", old: 12, new: 13, text: "    end = 4" },
  ]);
  assert.deepEqual(diffRows("@@ -1 +1 @@\n-a\n++++ b\n").at(-1), { kind: "added", old: null, new: 1, text: "+++ b" });
});

test("a new file reads as the file, a changed one as its diff", () => {
  assert.deepEqual(changeLayout(change("/ws/lib.py", "@@ -0,0 +1,3 @@\n+import os\n+\n+print(os)\n")), {
    kind: "newFile",
    text: "import os\n\nprint(os)",
    format: { kind: "code", language: "python" },
  });
  assert.equal(isNewFile(diffRows("@@ -0,0 +1 @@\n+x\n"), false), true);
  assert.equal(isNewFile(diffRows("@@ -1 +1,2 @@\n x\n+y\n"), false), false);
  assert.equal(changeLayout(change("/ws/app.ts", "@@ -1 +1 @@\n-let a = 1\n+let a = 2\n")).kind, "diff");
});

test("the format comes from the name; a picture shows from the Mac and a file without a diff says why", () => {
  assert.deepEqual(changeFormat("/a/notes.MD"), { kind: "markdown" });
  assert.deepEqual(changeFormat("/a/data.json"), { kind: "json" });
  assert.deepEqual(changeFormat("/a/shot.PNG"), { kind: "image" });
  assert.deepEqual(changeFormat("/a/report.pdf"), { kind: "pdf" });
  assert.deepEqual(changeFormat("/a/run.sh"), { kind: "code", language: "shell" });
  assert.deepEqual(changeFormat("/a/.env"), { kind: "code", language: null });
  assert.deepEqual(changeLayout(change("/a/shot.png", "", { note: "a binary file" })), { kind: "media", format: { kind: "image" } });
  assert.deepEqual(changeLayout(change("/a/shot.png", "", { gone: true, note: "gone" })), { kind: "note", note: "gone" });
});

test("JSON is laid out in its own order; JSON Lines stay as they are", () => {
  assert.equal(
    prettyJSON('{"b":1,"a":[1,2],"e":{},"s":"x, {y}: \\"z\\""}'),
    '{\n  "b": 1,\n  "a": [\n    1,\n    2\n  ],\n  "e": {},\n  "s": "x, {y}: \\"z\\""\n}',
  );
  assert.equal(prettyJSON('{"a":1}\n{"a":2}'), null);
});

/** The coloured pieces of `line`, plain ones left out. */
function kinds(line, language) {
  return highlightLines([line], language)[0]
    .filter((token) => token.kind !== "plain")
    .map((token) => `${token.kind}:${token.text}`);
}

test("code is coloured as the phone colours it", () => {
  assert.deepEqual(kinds('def f(x): return "a#b" + 42  # done', "python"), ["keyword:def", "keyword:return", 'string:"a#b"', "number:42", "comment:# done"]);
  const lines = highlightLines(['x = """start', "middle", 'end""" + y'], "python");
  assert.deepEqual(lines[1], [{ kind: "string", text: "middle" }]);
  assert.deepEqual(lines[2][0], { kind: "string", text: 'end"""' });
  assert.deepEqual(kinds('  "name": "dot", "on": true', "json"), ['key:"name"', 'string:"dot"', 'key:"on"', "keyword:true"]);
  assert.deepEqual(kinds("  - name: build # step", "yaml"), ["key:name", "comment:# step"]);
  assert.deepEqual(kinds("[tool.ruff]", "toml"), ["keyword:[tool.ruff]"]);
  assert.deepEqual(kinds("line-length = 120", "toml"), ["key:line-length", "number:120"]);
  assert.deepEqual(kinds("if [ $# -gt 0 ]; then echo 'hi'; fi # x", "shell"), ["keyword:if", "number:0", "keyword:then", "string:'hi'", "keyword:fi", "comment:# x"]);
  assert.deepEqual(kinds("## Title", "markdown"), ["heading:## Title"]);
  assert.deepEqual(kinds("- use `dotx` now", "markdown"), ["key:-", "string:`dotx`"]);
  assert.deepEqual(kinds('<a href="x">go</a> <!-- c -->', "html"), ["key:a", 'string:"x"', "key:a", "comment:<!-- c -->"]);
});
