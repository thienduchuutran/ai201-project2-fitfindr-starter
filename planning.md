# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
Filters the 40 mock secondhand listings by an optional size and an optional price ceiling, then ranks the survivors by keyword overlap against the user's description and returns the best matches first. This is pure local Python over `load_listings()` — it does **not** call the LLM.

**Input parameters:**
- `description` (str): Keywords describing the desired item (e.g., `"vintage graphic tee"`). Tokenized and scored against each listing's `title`, `style_tags`, and `description`.
- `size` (str | None): Size to filter by, matched **case-insensitively as a substring** so `"M"` matches `"S/M"` and `"M/L"`. `None` skips size filtering entirely.
- `max_price` (float | None): Inclusive price ceiling — keep listings where `price <= max_price`. `None` skips price filtering.

**What it returns:**
A `list[dict]` of matching listings sorted by relevance score, highest first. Each dict has: `id`, `title`, `description`, `category`, `style_tags` (list), `size`, `condition`, `price` (float), `colors` (list), `brand` (str or None), `platform`. Returns an empty list `[]` when nothing matches — it never raises.

**What happens if it fails or returns nothing:**
On an empty list, the *agent* (not the tool) sets `session["error"] = "No listings found for [query]. Try broader keywords, a different size, or raising your price limit."` and returns early. `suggest_outfit` and `create_fit_card` are never called — the agent does not pass empty input downstream.

---

### Tool 2: suggest_outfit

**What it does:**
Takes the selected listing plus the user's wardrobe and asks the Groq LLM to propose 1–2 complete outfits that combine the new item with specific, named pieces the user already owns.

**Input parameters:**
- `new_item` (dict): The selected listing dict (`session["selected_item"]`). Supplies `title`, `category`, `colors`, and `style_tags` as styling context.
- `wardrobe` (dict): A wardrobe dict with an `"items"` key holding a list of wardrobe-item dicts (`id`, `name`, `category`, `colors`, `style_tags`, `notes`). May be empty (`{"items": []}`).

**What it returns:**
A non-empty `str` of outfit suggestions. When `wardrobe["items"]` is empty, it returns **general styling advice** for the item (what pairs well, what vibe it suits) instead of specific combinations — never an empty string.

**What happens if it fails or returns nothing:**
An empty wardrobe is **not** a failure — it triggers the general-advice path so the flow continues. If the LLM call itself fails (network/API error), retry once; if it still fails, return the string `"Couldn't generate outfit suggestion. Please try again."` rather than raising.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion and the selected item into a short, casual Instagram/TikTok-style caption for the thrifted find, using a higher LLM temperature so captions vary between runs.

**Input parameters:**
- `outfit` (str): The outfit suggestion string returned by `suggest_outfit` (`session["outfit_suggestion"]`).
- `new_item` (dict): The selected listing dict. Supplies the item name, price, and platform to mention naturally (once each) in the caption.

**What it returns:**
A 2–4 sentence caption `str` that feels like a real OOTD post — casual and authentic, naming the item, its price, and the platform once each, and capturing the outfit vibe in specific terms.

**What happens if it fails or returns nothing:**
If `outfit` is empty or whitespace-only, it returns the string `"Outfit data is missing — can't generate a fit card."` instead of raising. The agent never calls this tool unless `suggest_outfit` returned a non-empty string, so this guard is a defensive backstop.

---

### Additional Tools (if any)

None for the core build. The three required tools cover the full pipeline end to end: retrieve (`search_listings`) → style (`suggest_outfit`) → caption (`create_fit_card`). A possible stretch tool would be `parse_query` (LLM-based extraction of description/size/max_price), but the core build does parsing inline in the planning loop with regex/string splitting.

---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop is **conditional, not a fixed sequence** — each tool runs only if the previous step produced usable output, and `session["error"]` is the short-circuit channel. At every step the agent looks only at the contents of `session`: whether the relevant slot is non-empty and whether `error` is still `None`.

1. **Init** — `session = _new_session(query, wardrobe)`.
2. **Parse** — extract `description`, `size`, `max_price` from `query` → `session["parsed"]`. (`size`/`max_price` may be `None` when the user doesn't specify them.)
3. **Search** — call `search_listings(description, size, max_price)` → `session["search_results"]`.
   - **GATE 1:** if `search_results` is empty → set `session["error"]`, **return immediately**. `suggest_outfit` and `create_fit_card` never run.
4. **Select** — `session["selected_item"] = search_results[0]` (top-scored match).
5. **Suggest** — call `suggest_outfit(selected_item, wardrobe)` → `session["outfit_suggestion"]`.
   - **GATE 2:** if the returned string is empty → set `session["error"]`, **return**. (An empty *wardrobe* is not empty *output* — it returns advice, so it passes this gate.)
6. **Caption** — call `create_fit_card(outfit_suggestion, selected_item)` → `session["fit_card"]`.
7. **Return** the completed `session`.

**How it knows it's done:** the run terminates either successfully (when `fit_card` is populated) or early (when `error` is set at GATE 1 or GATE 2). There is no looping/branching beyond these gates — it is a linear pipeline with two early-exit points.

---

## State Management

**How does information from one tool get passed to the next?**

The `session` dict is the **single source of truth** for one interaction. `_new_session()` seeds it with empty slots; each step **writes** its output to a named slot, and the next step **reads** its input from a prior slot. Nothing is passed via globals or by threading return values through deep call chains — everything flows through `session`.

| Slot | Written by | Read by | Notes |
|------|-----------|---------|-------|
| `query` | init | parse step | Raw user input; set once, never mutated. |
| `parsed` | step 2 | `search_listings` | `{"description": str, "size": str\|None, "max_price": float\|None}`. |
| `search_results` | `search_listings` (step 3) | select step / GATE 1 | List of listing dicts; `[]` triggers GATE 1. |
| `selected_item` | step 4 | `suggest_outfit`, `create_fit_card`, UI | `search_results[0]` — the hand-off into Tool 2. |
| `wardrobe` | init | `suggest_outfit` | Passed in at start (example or empty). |
| `outfit_suggestion` | `suggest_outfit` (step 5) | `create_fit_card`, UI / GATE 2 | The hand-off into Tool 3. |
| `fit_card` | `create_fit_card` (step 6) | UI | Final generated output. |
| `error` | any gate | both front-ends, all gates | `None` on success; a helpful string on early exit. Once set, the loop returns and downstream slots stay `None`. |

**Hand-off chain:** `query → parsed → search_results → selected_item → outfit_suggestion → fit_card`. At the end the UI reads `selected_item` / `outfit_suggestion` / `fit_card` on success, or `error` on failure.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Agent sets `session["error"] = "No listings found for [query]. Try broader keywords, a different size, or raising your price limit."`, returns early, and **skips** `suggest_outfit` and `create_fit_card`. |
| suggest_outfit | Wardrobe is empty | **Not** treated as an error — the tool returns general styling advice for the item so the flow continues to `create_fit_card`. (If the LLM call itself fails: retry once, then return `"Couldn't generate outfit suggestion. Please try again."`) |
| create_fit_card | Outfit input is missing or incomplete | Tool returns `"Outfit data is missing — can't generate a fit card."` as the caption string instead of raising, so the UI still shows a clear message. |

---

## Architecture

```
                       user query (natural language)
                                    │
                                    ▼
                 ┌───────────────────────────────────────┐
                 │  run_agent(query, wardrobe)            │
                 │  _new_session()  →  session{}          │
                 └───────────────────┬───────────────────┘
                                     ▼
                 ┌───────────────────────────────────────┐
                 │  parse query                          │  writes session["parsed"]
                 │  {description, size, max_price}        │  (size / max_price may be None)
                 └───────────────────┬───────────────────┘
                                     ▼
                 ┌───────────────────────────────────────┐
                 │  search_listings(description,         │  reads parsed
                 │      size, max_price)                  │  LOCAL DATA — no LLM
                 └───────────────────┬───────────────────┘
                                     │ writes session["search_results"]
                          ┌──────────┴───────────┐
                  empty   │                      │  has results
                  list?   ▼                      ▼
        ┌──────────────────────────┐   selected_item = search_results[0]
        │ session["error"] =       │   → session["selected_item"]
        │  "No listings found..."  │              │
        │ RETURN early  ───────────┼──► (end)     ▼
        └──────────────────────────┘   ┌───────────────────────────────────┐
                                        │  suggest_outfit(selected_item,    │  reads selected_item
                                        │      wardrobe)                     │  + wardrobe — GROQ LLM
                                        │  empty wardrobe → general advice   │
                                        │  LLM fails → retry once → err str  │
                                        └──────────────────┬─────────────────┘
                                                           │ writes session["outfit_suggestion"]
                                                ┌──────────┴───────────┐
                                        empty   │                      │  non-empty string
                                        string? ▼                      ▼
                              ┌──────────────────────────┐   ┌───────────────────────────────┐
                              │ session["error"] set     │   │  create_fit_card(outfit,      │  reads outfit + item
                              │ RETURN early ───► (end)  │   │      new_item)                 │  GROQ LLM, high temp
                              └──────────────────────────┘   │  empty outfit → error string   │
                                                             └────────────────┬───────────────┘
                                                                              │ writes session["fit_card"]
                                                                              ▼
                                                                  ┌────────────────────────┐
                                                                  │  RETURN session         │
                                                                  └───────────┬────────────┘
                                                                              ▼
                                              UI panels:  🛍️ listing  │  👗 outfit  │  ✨ fit_card
                                              (on error: error message in panel 1, others blank)
```

**Reading the diagram:** the spine flows top → bottom and every box reads from / writes to the shared `session`. Error branches split off to the **left** at each gate (empty search results, empty outfit string) and short-circuit straight to `RETURN`. The two LLM-backed tools are on the main path; `search_listings` is the only local-data box.

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
- **Tool:** Claude (Claude Code in VS Code).
- **`search_listings`** — I'll give Claude the Tool 1 spec block (inputs/types, return shape, `[]`-not-exception failure mode) **plus the data quirks I found in Milestone 1**: sizes are free-form so size matching must be a case-insensitive substring check; `style_tags` + `title` + `description` are the keyword-overlap fields; `brand` can be `None`. I expect a pure-Python implementation built on `load_listings()`. **Verify before trusting:** run 3 queries — `"vintage graphic tee under $30"` (expect graphic-tee hits like `lst_006`), `"designer ballgown under $5"` (expect `[]`), and a `size="M"` filter (expect `"S/M"`/`"M/L"` included). Confirm `[]` is returned, not an exception, on no match.
- **`suggest_outfit` + `create_fit_card`** — I'll give Claude the Tool 2/3 spec blocks plus the **listing-vs-wardrobe shape asymmetry** (listings use `title`/`price`/`platform`; wardrobe items use `name`/`notes`; each shape must be formatted separately). I'll point it at the existing `_get_groq_client()`. I expect prompt-building + a Groq call per tool. **Verify:** call each tool standalone with `get_example_wardrobe()` **and** `get_empty_wardrobe()`; confirm the empty-wardrobe branch returns advice (not a crash), and that `create_fit_card("")` returns the error string rather than raising.

**Milestone 4 — Planning loop and state management:**
- **Tool:** Claude.
- **Input:** the Planning Loop section (the two conditional GATES), the State Management table (slots + hand-off chain), and the Architecture diagram. I expect Claude to implement `run_agent()` so it matches the gates exactly — empty search → `error` + return; empty outfit → `error` + return; otherwise fill `fit_card`.
- **Verify:** run `agent.py`'s `__main__` block (happy path + the deliberate no-results query). Confirm the no-results run sets `error` and leaves `outfit_suggestion`/`fit_card` as `None`, and the happy path fills all three slots. Then wire `app.py`'s `handle_query` and re-test the same queries through the Gradio UI before considering it done.

---

## A Complete Interaction (Step by Step)

Write out what a full user interaction looks like from start to finish — tool call by tool call. Use a specific example query.

**What FitFindr does (plain English):** FitFindr takes one natural-language request for a secondhand clothing item, finds the best-matching real listing, then uses the user's existing wardrobe to suggest how to style it and writes a shareable caption for the find. The user's typed query triggers `search_listings`, then the top search result triggers `suggest_outfit` (using the chosen wardrobe), that outfit text triggers `create_fit_card`. All results accumulate in a single `session` dict that is passed forward step to step, and if any step produces nothing usable, most importantly when the search returns zero matches, the agent stores a message in `session["error"]`, stops early, and skips the remaining tools instead of feeding empty input downstream.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — parse + search.** The agent parses the query into `description="vintage graphic tee"`, `size=None`, `max_price=30.0` and stores them in `session["parsed"]`. It calls `search_listings("vintage graphic tee", None, 30.0)`, which filters the 40 listings by price ≤ $30, scores the rest by keyword overlap, and returns matches (e.g. `lst_006` "Graphic Tee - 2003 Tour Bootleg Style", $24) into `session["search_results"]`. **Failure path:** if that list is empty, set `session["error"]` to a helpful "no matches" message and return now — do not continue.

**Step 2 — select + suggest.** The agent picks the top-scored result into `session["selected_item"]` and calls `suggest_outfit(selected_item, wardrobe)`. With the example wardrobe it pairs the tee with named pieces (baggy jeans `w_001`, chunky sneakers `w_007`, denim jacket `w_006`); the LLM's outfit text is stored in `session["outfit_suggestion"]`. **Failure path:** an empty wardrobe doesn't error — the tool returns general styling advice instead.

**Step 3 — caption.** The agent calls `create_fit_card(outfit_suggestion, selected_item)`, which uses the item's title, price, and platform plus the outfit text to generate a casual OOTD caption, stored in `session["fit_card"]`. **Failure path:** if the outfit string is empty/whitespace, the tool returns a descriptive error string rather than raising.

**Final output to user:** The three panels in the UI show the formatted top listing (title, price, condition, platform), the outfit suggestion, and the fit-card caption, read directly from `session["selected_item"]`, `session["outfit_suggestion"]`, and `session["fit_card"]`. If `session["error"]` was set, the user instead sees that one message and the other two panels stay empty.
