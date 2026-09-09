# Main browser integration — intermediate run

2026-09-09. Real browser via CUA, UI dev server and isolated owned Mongo/runtime, no external APIs or blockchain writes.

## Confirmed

- `http://127.0.0.1:3200/request` first POST rejected403 origin not allowed: runtime default permits localhost/127.0.0.1 port3000. This was test configuration, not a request UI bug. Stopped only our3200dev server; port3000 was free; restarted there without weakening origin protection.
- Browser `/request` with automatic priority, budget0.01, explicit Mock consent created `c79aa88e-53f3-41b1-9360-b51a7e85e594`, completed `/run`, navigated to detail.
- Prompt classification price (`비용 최소화`), 27 estimated input/1024 maxoutput, amount619units /0.000619AEGIS, three model rows/scores/weights and eight lifecycle events shown. Overview shows one Mocksettlement, unknown balance as dash, no purchase form.
- This server started BEFORE03 original-priority-audit implementation. Its existing CAUTION about missing raw-request rederivation is expected; do not rewrite this prior audit to hide it. Final browser run must use the final server and a new purchase.

## UI corrections before final browser acceptance

1. At viewport360x800, DOM `innerWidth=360`, document/body `scrollWidth=764`: whole-page horizontal overflow. Screenshot shows top navigation squeezed to a tiny scroll area; recent table/grid forces body width. Keep horizontal scrolling local to a table when needed, constrain grid children/min-width, and make nav reachable on narrow screens. Preserve existing visual style; do not mask content with global overflow:hidden. Verify overview, request, detail at360 and a desktop width.
2. New shell still labels `/agents` as `판매 에이전트`, suggesting an active new-flow seller/reputation module. Rename this historical-only navigation/route copy explicitly or remove the nav link while retaining historical detail evidence. Do not present ERC8004 as active new flow.

No screenshots have yet been committed; capture the final UI after corrections. The temporary Mongo instance owns only test data and must be stopped/cleaned safely or explicitly reported as retained demonstration storage.

## Corrections and cleanup verified

- UI commit `9f6e23f`: all35 focused UI tests pass; independent pending-lookup review approve (13 focused behavior tests). Old PBLC keys remain read-only; initial lookup blocks both button and handler.
- Real CUA browser: viewport360, document widths overview345/request345/detail360 (no page overflow). Mobile navigation is now a separate full-width row; historical agent label visible. Desktop1440 document1425, no overflow. Tables retain their own horizontal scroll.
- Main Next dev session8784 exited0 before final build gate opened. Main owned stack70494 stopped via Ctrl-C; its temporary database/test purchase `c79aa88e-53f3-41b1-9360-b51a7e85e594` belongs only to this disposable run, not user's history. Final browser acceptance will create a new purchase on the final backend rather than rewrite that earlier audit.
