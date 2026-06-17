# FitFindr

FitFindr is a multi-tool AI agent that takes a natural-language request for a secondhand clothing item, finds the best-matching listing, suggests how to style it with the user's existing wardrobe, and writes a shareable caption for the find.

It runs three tools in sequence behind a single planning loop, passing state forward through one `session` dict. The LLM calls use Groq `llama-3.3-70b-versatile`.

## Setup

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows:     source .venv/Scripts/activate
pip install -r requirements.txt
```

Add a Groq API key (free at [console.groq.com](https://console.groq.com)) to a `.env` file in the project root:

```
GROQ_API_KEY=your_key_here
```

Run the app, then open the localhost URL it prints (usually http://localhost:7860):

```bash
python app.py
```

CLI smoke test (happy path + no-results path):

```bash
python agent.py
```

Tests:

```bash
pytest tests/ -v
```

## How It Works

```
user query → run_agent (planning loop) → search_listings → suggest_outfit → create_fit_card → session
                                              (local)          (Groq LLM)        (Groq LLM)
```

`search_listings` is deterministic local data; `suggest_outfit` and `create_fit_card` call the LLM. Every step reads from and writes to the shared `session` dict, and two gates short-circuit the loop on failure.

## Tool Inventory

### Tool 1 — `search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]`

- **Input:**
  - `description` (str) — keywords describing the item
  - `size` (str | None) — size filter, case-insensitive substring match
  - `max_price` (float | None) — price ceiling, inclusive
- **Output:** `list[dict]` — matching listing dicts sorted by relevance score descending. Each dict has: `id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`. Returns `[]` if nothing matches — never raises.
- **Purpose:** Filters and scores 40 mock listings against the user's query using keyword overlap across the `title`, `description`, `style_tags`, `colors`, and `brand` fields.

### Tool 2 — `suggest_outfit(new_item: dict, wardrobe: dict) -> str`

- **Input:**
  - `new_item` (dict) — a listing dict from `search_listings`
  - `wardrobe` (dict) — wardrobe dict with an `'items'` key
- **Output:** `str` — an outfit suggestion. If the wardrobe is empty, returns general styling advice. Never returns an empty string, never raises.
- **Purpose:** Calls the Groq LLM (temp = 0.7) to suggest 1–2 outfit combinations using the new item and the user's existing wardrobe pieces.

### Tool 3 — `create_fit_card(outfit: str, new_item: dict) -> str`

- **Input:**
  - `outfit` (str) — the suggestion string from `suggest_outfit`
  - `new_item` (dict) — the listing dict
- **Output:** `str` — a 2–4 sentence Instagram/TikTok-style caption. Returns a specific error string if `outfit` is empty. Never raises.
- **Purpose:** Calls the Groq LLM (temp = 1.0) to generate a casual, shareable caption mentioning the item name, price, and platform once each.

## Planning Loop

The loop is conditional, not sequential. After each tool call there is an explicit gate:

- **Gate 1:** if `search_listings` returns `[]` → set `session["error"]`, return immediately, never call `suggest_outfit`.
- **Gate 2:** if `suggest_outfit` returns an empty or error string → set `session["error"]`, return immediately, never call `create_fit_card`.

Only if both gates pass does the agent reach `create_fit_card` and return a complete session.

## State Management

All state lives in a single `session` dict initialized at the start of each run. Keys:

- `query` — raw input, never changes
- `parsed` — LLM-extracted `description` / `size` / `max_price`
- `search_results` — full filtered list
- `selected_item` — `results[0]`, passed into `suggest_outfit`
- `wardrobe` — passed in at start, never modified
- `outfit_suggestion` — string from `suggest_outfit`, passed into `create_fit_card`
- `fit_card` — final output string
- `error` — `None` on success, a message string on any failure

State passes forward by storing tool output in the `session` dict and reading it in the next step — no global variables, no re-prompting the user.

## Error Handling

- **`search_listings`** — returns `[]` on no match. The agent sets `error: "No listings found for '{description}'. Try broader keywords, a different size, or raising your price limit."`
  *Verified:* query `"designer ballgown size XXS under $5"` → Panel 1 shows the error, Panels 2 & 3 empty.
- **`suggest_outfit`** — empty wardrobe → general styling advice instead of a crash.
  *Verified:* empty wardrobe + `"90s track jacket"` query → Panel 2 shows category-level advice with no named wardrobe pieces.
- **`create_fit_card`** — empty outfit string → returns `"Outfit data is missing — can't generate a fit card."`
  *Verified:* direct call with `""` → returns the guard string, no exception.

## Spec Reflection

**One way `planning.md` helped:** Designing the `session` dict keys before writing any code meant every tool knew exactly what field to read and write. When wiring `agent.py`, there were no naming conflicts or ambiguous handoffs — `selected_item` was always `selected_item`.

**One divergence:** The planning loop uses LLM-based query parsing (Groq extracts `description`/`size`/`max_price` as JSON) rather than regex. This was chosen because natural-language queries are too varied for reliable regex — "under $30", "$30 max", and "less than 30 dollars" would all need separate patterns, while the LLM handles all of them with a single prompt. Tradeoff: it adds one extra API call per query and requires a JSON-parsing fallback.

## AI Usage

**Instance 1 — `search_listings` implementation**
- *Input to Claude:* Tool 1 spec from `planning.md` (inputs, scoring logic, failure mode) + instruction to use `load_listings()`.
- *Output:* a working filter + score implementation.
- *What I changed:* the size matching — the generated code used exact equality, and I changed it to a case-insensitive substring match (`.lower() in .lower()`) because listings use formats like `"S/M"` and `"XL (oversized)"`, not normalized sizes.

**Instance 2 — `run_agent()` planning loop**
- *Input to Claude:* the full `session` dict structure + the planning-loop conditional logic + the agent diagram from `planning.md`.
- *Output:* a working planning loop with gates.
- *What I changed:* added LLM-based query parsing instead of regex, and added a JSON-parsing fallback for when the LLM returns malformed JSON.
