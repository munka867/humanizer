// Boot: build ctx, shell, router; start event stream. Debug handle: window.__cc = {ctx, shell, router}.
import { createCtx } from "./ctx.js";
import { createShell } from "./shell.js";
import { createRouter } from "./router.js";
import { fmt } from "./format.js";

const qs = new URLSearchParams(location.search);
const staleAfterS = Number(qs.get("cc_stale_after")) > 0 ? Number(qs.get("cc_stale_after")) : undefined; // test hook

const ctx = createCtx({ staleAfterS });
await Promise.race([ctx.prefs.load(), new Promise((r) => setTimeout(r, 2000))]);
fmt.tz(ctx.prefs.get("ui.tz", "America/New_York"));
const shell = createShell(ctx);
const router = createRouter({ ctx, outlet: shell.outlet, onRoute: shell.onRoute });
window.__cc = { ctx, shell, router };
ctx.env.start();
ctx.events.start();
router.render();
