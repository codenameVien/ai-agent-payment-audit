import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cssUrl = new URL("../src/app/globals.css", import.meta.url);
const navUrl = new URL("../src/components/Nav.tsx", import.meta.url);

/** The declaration block of a top-level rule, matched at the start of a line. */
function ruleBlock(css, selector) {
  const match = new RegExp(`(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{`).exec(css);
  assert.notEqual(match, null, `${selector} rule is missing`);
  const start = match.index + match[0].length;
  return css.slice(start, css.indexOf("}", start));
}

function mediaBlock(css, query) {
  const start = css.indexOf(query);
  assert.notEqual(start, -1, `${query} block is missing`);
  const next = css.indexOf("@media", start + query.length);
  return css.slice(start, next === -1 ? css.length : next);
}

test("no ancestor masks the overflow the narrow layout has to solve", async () => {
  const css = await readFile(cssUrl, "utf8");
  for (const selector of ["html", "body", "main"]) {
    assert.doesNotMatch(ruleBlock(css, selector), /overflow/);
  }
});

test("horizontal scrolling stays inside the table wrapper", async () => {
  const css = await readFile(cssUrl, "utf8");
  const wrap = ruleBlock(css, ".tableWrap");
  assert.match(wrap, /overflow-x: auto/);
  assert.match(wrap, /max-width: 100%/);
  assert.match(wrap, /min-width: 0/);
  // The wide table keeps its readable width; it just may not push the document.
  assert.match(css, /table \{ border-collapse: collapse; width: 100%; min-width: 700px; \}/);
});

test("every grid track can shrink below its content", async () => {
  const css = await readFile(cssUrl, "utf8");
  const declarations = [...css.matchAll(/grid-template-columns:([^;]+);/g)].map(
    (match) => match[1].trim(),
  );
  assert.ok(declarations.length >= 10);
  for (const declaration of declarations) {
    // A bare `1fr` track keeps an automatic min-content floor, which is what let a
    // 700px table widen the whole document at 360px.
    const withoutMinmax = declaration.replace(/minmax\([^)]*\)/g, "");
    assert.doesNotMatch(
      withoutMinmax,
      /\d*\.?\d*fr/,
      `unconstrained fr track: grid-template-columns: ${declaration}`,
    );
  }
});

test("flex and grid containers that can hold a table are allowed to shrink", async () => {
  const css = await readFile(cssUrl, "utf8");
  assert.match(ruleBlock(css, ".panel"), /min-width: 0/);
  assert.match(css, /\.auditPrimary, \.auditSidebar \{[^}]*min-width: 0/);
  assert.match(css, /\.twoCol > \* \{ min-width: 0; \}/);
  assert.match(ruleBlock(css, "dd"), /overflow-wrap: anywhere/);
  assert.match(ruleBlock(css, ".auditIntro h1, .pageHead h1"), /overflow-wrap: break-word/);
});

test("the narrow breakpoint keeps the navigation reachable", async () => {
  const css = await readFile(cssUrl, "utf8");
  const narrow = mediaBlock(css, "@media (max-width: 800px)");
  assert.match(narrow, /\.brand \{ min-width: 0; \}/);
  // `flex: 1` on the base rule keeps a zero basis, so the navigation needs an explicit
  // full basis to get its own row next to the brand and the live status.
  assert.match(narrow, /\.topbar nav \{ order: 3; flex: 0 0 100%; min-width: 0; overflow: auto/);
  for (const selector of [".walletList", ".twoCol", ".alert"]) {
    assert.match(
      narrow,
      new RegExp(`\\${selector} \\{ grid-template-columns: minmax\\(0, 1fr\\); \\}`),
    );
  }
});

test("the navigation links exactly the audit surfaces that still exist", async () => {
  const nav = await readFile(navUrl, "utf8");
  const links = [...nav.matchAll(/<Link[^>]*href="([^"]+)"/g)].map((match) => match[1]);
  // Brand link first, then the menu: overview, purchases, alerts. No seller agent,
  // login or experiments screen is reachable from the shell any more.
  assert.deepEqual(links, ["/", "/", "/purchases", "/alerts"]);
});

test("styles of the removed screens are gone with them", async () => {
  const css = await readFile(cssUrl, "utf8");
  assert.doesNotMatch(
    css,
    /\.(agentCard|agentIcon|loginCard|centerPage|loginHelp|cardGrid|experimentLayout|experimentSteps)\b/,
  );
});
