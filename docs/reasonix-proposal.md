# Reasonix proposal — a tool-transform layer

> Draft discussion post for **[esengine/DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)**.
> Status: ready to post. Target: GitHub **Discussions → Ideas**
> (`https://github.com/esengine/DeepSeek-Reasonix/discussions`).
>
> Why Discussions, not an Issue: the repo sets `blank_issues_enabled: false` and
> its `config.yml` routes "ideas / general discussion" to Discussions explicitly.
> Issue templates (Bug / Feature) can't hold a prototype + landing sketch.
>
> Before posting: replace `<your tweet URL>` with the real link, attach a
> screenshot (mcpp admin UI, or a before/after tool list).

---

**Title:** [discussion] A tool-transform layer for Reasonix? (working prototype + landing sketch)

Hi 👋 — thanks for Reasonix; the cache-first design is exactly what I wanted.

Opening a **directional discussion**: should Reasonix add a **tool transform** layer in the MCP client — between `tools/list` and the model — letting users rewrite a tool's description, hide/discretize params, and declare inter-tool relationships, **without touching upstream MCP server code**?

**Not presupposing how it lands** — native / external plugin / some third option you prefer — all on the table. I bring a <500-line working prototype proving the path is tractable, plus a **native-landing technical sketch** (in the collapsible below) as a reference.

**Why Reasonix specifically:** it's DeepSeek-native. DeepSeek *does what you say, not what you mean* — a 3-line description with 10 params won't be "grokked" like Claude does; it reasons literally and mis-picks. The community reads this as "MCP is dead," but what's dead is bad tool design, not the protocol. You can't fix upstream — but you can transform at the harness layer.

### The thing I most want to push: an inter-tool relationship graph

After writing and using my own MCP tools a lot, what I found **actually works** isn't tweaking a single tool's params — it's **referencing other tools by backtick in a tool's description, writing the inter-tool relationship as a literal instruction**. A hand-written micro knowledge graph. Four kinds of "edges":

- **Sequential**: "first call `A` to extract data, then this tool to convert to Excel"
- **Mutex**: "image analysis only — for documents use `mineru/extract`, do NOT use this tool on docs"
- **Fallback**: "if `A` is unavailable, use `B` instead"
- **Classifier-fronted**: "first call `classifier` to decide type, then route by result"

Why I think this is especially valuable for Reasonix (DeepSeek-native): DeepSeek *does what you say, not what you mean* — "first A then B" / "documents go to mineru" are **literal if-else**, still honored at turn 500, no "grokking" needed. Each edge is ~12 tokens and turns tool selection from "semantic retrieval" into "hard routing." Vector search answers "which tools *might* be relevant"; explicit references answer "which tool to call *right now*" — for a literal-execution model, the latter is far more reliable.

This is the trick that paid off most for me when building MCP tools, so it's the one I most want your read on. Reasonix doesn't have this layer today (grepped `internal/tool`/`internal/plugin`/`internal/config` — no inter-tool reference mechanism), and upstream tools can't be edited, so it has to live in the harness.

### Three supporting transforms (also in the prototype, but secondary)

1. **Param pruning** — `hidden:true`+`default`; agent neither chooses nor sees them.
2. **N:1 presets** — bundle several upstream params into one downstream enum. The big one: expose `mode: fast|standard|deep` instead of making the agent pick `model` × `effort` × `temperature` separately. One choice, a whole coordinated parameter set fires underneath.
3. **Continuous→discrete** — `temperature:0.0–2.0` → `style:precise|balanced|creative` (1:1 value mapping, lighter than a preset).

Concretely, the N:1 preset looks like this in the prototype (YAML; would become toml in Reasonix):
```yaml
params:
  - name: mode
    type: preset
    preset:
      fast:     { model: flash,  effort: low,  temperature: 0.1 }
      standard: { model: flash,  effort: high, temperature: 0.7 }
      deep:     { model: pro,    effort: max,  temperature: 0.3 }
```
The agent sees one `mode` enum and picks semantically ("fast" vs "deep"); on `tools/call` the harness expands it back into `model`+`effort`+`temperature` for upstream. This is where the decision-space collapse is largest — 3×3×(continuous) collapsed to 3.

**Questions:**
1. Do you buy the direction?
2. Which shape do you lean toward — (a) native `[tool_transform]` in `reasonix.toml`, (b) mcpp as external MCP plugin, (c) your own third option?
3. **Cache:** transform output enters the cache-stable prefix. My instinct: resolve transforms **once at config load** so runtime schema stays byte-stable. Want your read on the boundary.

If interested, I'll implement (native = I'll pick up Go; plugin = I add a spec-compliant entry). If not, fine — at least it's on the record.

---

<details>
<summary><b>Appendix: native-landing sketch (not a PR — just showing I've thought it through)</b></summary>

Looked at Reasonix's schema flow — the native insertion point is clean (one spot, zero caller changes):

- `provider.ToolSchema` is just `Name`/`Description`/`Parameters` (`internal/provider/provider.go:90`) — exactly the three fields transform touches.
- Wrap `registry.Schemas()` to cover all call sites (`agent.go:929/1563`, `coordinator.go:383`, `subagent_store.go:770`).
- Reverse param map in plugin-tool Execute path; built-ins untouched.
- **Cache**: resolve once at config load, cache result, runtime schema byte-stable → prefix cache unaffected. PR would carry `Cache-impact: low` + a guard test asserting schema-hash stability.

Estimated footprint: new `internal/tool/transform` (pure funcs + unit tests), `ToolTransform` in `internal/config`, wrap `registry.Schemas()`, reverse-map in plugin Execute, a `reasonix.example.toml` example. Logic mirrored from mcpp's `transform.py`, a few hundred lines of Go.

(Again: not asking you to take a PR now — just showing I come with the direction thought through, not an empty probe.)
</details>

**Links**
- mcpp prototype + design doc: https://gitcode.com/Joe-zhouman/mcpp
- Long-form on why this matters more for DeepSeek: [微信公众号](https://mp.weixin.qq.com/s/ot2tey-3ZtqBah7Gbpm4SA)
