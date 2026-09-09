import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cssUrl = new URL("../src/app/globals.css", import.meta.url);
const navUrl = new URL("../src/components/Nav.tsx", import.meta.url);
const agentsUrl = new URL("../src/components/agents.tsx", import.meta.url);
const agentsPageUrl = new URL("../src/app/agents/page.tsx", import.meta.url);

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
  assert.match(css, /\.twoCol > \*, \.experimentLayout > \*, \.cardGrid > \* \{ min-width: 0; \}/);
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
  for (const selector of [".walletList", ".twoCol", ".experimentLayout", ".cardGrid", ".alert"]) {
    assert.match(
      narrow,
      new RegExp(`\\${selector} \\{ grid-template-columns: minmax\\(0, 1fr\\); \\}`),
    );
  }
});

test("seller agent navigation is labelled as historical, not an active module", async () => {
  const [nav, agents, page] = await Promise.all([
    readFile(navUrl, "utf8"),
    readFile(agentsUrl, "utf8"),
    readFile(agentsPageUrl, "utf8"),
  ]);
  assert.match(nav, /<Link href="\/agents">과거 판매 에이전트<\/Link>/);
  assert.doesNotMatch(nav, />판매 에이전트</);
  // ERC-8004 is not presented as part of the new flow.
  assert.doesNotMatch(agents, /eyebrow">ERC-8004</);
  assert.match(agents, /신규 aa-three-factor-v1 흐름에는 판매 에이전트 협상과 ERC-8004 평판 실행이 없습니다/);
  assert.match(agents, /과거 PBLC 거래에 저장된 신원·평판 증거를 읽기 전용으로/);
  assert.doesNotMatch(agents, /RPC 연결 후 온체인 평판 표시/);
  // The route stays so the stored historical evidence remains reachable.
  assert.match(page, /<Agents/);
});
