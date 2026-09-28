# Ticker

A news-style scrolling ticker overlay. You can place it anywhere on the screen,
style it however you like, and feed it from the panel, a bot, a JSON file or a
JSON URL.

- **Panel:** `http://<host>:4747/ticker`
- **OBS browser source:** `http://<host>:4747/ticker/overlay` (1920×1080; it
  scales to any size)

## How it works

What scrolls is a list of **feeds**. A feed is a named list of lines (say
`news`, `balances` or `announce`). Enabled feeds scroll one after another in
the panel's order, and each one is led by its **label** badge (for example
`💰 HEXBANK`).

The overlay pulls the *next* line from the live list each time a line scrolls
off. So when the bot changes something, the change appears mid-scroll without
the ticker restarting or jumping:

- **New lines** (up to five arriving at once) jump the queue and scroll next.
  Replacing a whole feed doesn't trigger this; its lines simply take their turn.
- **A line already on screen whose text changes** (say a balance going up) is
  updated in place.

### Lines

Each line has:

| field | meaning |
|---|---|
| `text` | Shown as-is. If blank, the line is built from the feed's `template`. |
| `key` | Optional. Unique within the feed, so writing the same key again **updates** that line instead of adding a new one. |
| `value` | Optional. Numbers keep their number type, so sorting works, and are shown with thousands separators (`1,300`). |
| `color` | Optional text color for this line (for example `#ffd23f`). |
| `ttl` | Optional number of seconds before the line removes itself. |

### Feed settings

| field | meaning |
|---|---|
| `label` | Badge drawn before the feed's lines (blank means no badge). |
| `template` | Builds lines that have no text. `{key}`, `{value}`, `{text}` and `{label}` are filled in. Default `{key}: {value}`. |
| `sort` | `none` (as added), `value_desc`, `value_asc`, `key` or `newest`. |
| `limit` | Show at most N lines (0 means all), for example the top 20 balances. |
| `color` | Default color for this feed's lines. |
| `enabled` | Whether the feed is on air. Turning it off keeps its lines. |
| `source` | Optional JSON URL to pull the lines from. See [Pulling from a URL](#pulling-from-a-url). |

## API

Every call works as a plain **GET with query parameters** or a **POST with the
same fields as JSON**. This is handy for bots that can only fetch URLs. A feed
is created the first time something writes to it.

```
GET|POST /ticker/api/say?text=...&ttl=60&color=#ffd23f
         One-off announcement in the "announce" feed. It scrolls next and is
         removed after ttl seconds (default 60; ttl=0 keeps it).

GET|POST /ticker/api/feed/{name}/add?text=...           add a line
GET|POST /ticker/api/feed/{name}/add?key=alice&value=1300
GET|POST /ticker/api/feed/{name}/set?key=alice&value=1300
         Same as add. With a key it's an upsert: the same key updates in place.
         Other params: color, ttl, first=1 (put at the front).

POST|PUT /ticker/api/feed/{name}
         Create or update a feed. Settings you send are changed; the rest are
         kept. "items": [...] replaces the lines (strings or objects).
         "values": {"alice": 1300, ...} replaces them with keyed lines.

GET|POST /ticker/api/feed/{name}/remove?id=...|key=...|text=...
GET|POST /ticker/api/feed/{name}/clear
GET|POST /ticker/api/feed/{name}/enable?on=0|1
GET|POST /ticker/api/feed/{name}/refresh    poll its URL source now
GET|POST|DELETE /ticker/api/feed/{name}/delete

GET  /ticker/api/feed/{name}     one feed, plus its rendered lines
GET  /ticker/api/feeds           every feed
POST /ticker/api/feeds           replace every feed: {"feeds": [...]}
POST /ticker/api/feeds/order     {"order": ["announce", "balances", "news"]}
GET  /ticker/api/items           exactly what is scrolling right now
GET|POST /ticker/api/show · /ticker/api/hide
GET  /ticker/api/status          overlays connected, visible, feed and line counts
GET|POST /ticker/api/config      style (merge-and-save any subset of keys)
```

Feed names are 1–40 characters of `a-z`, `0-9`, `_` or `-`. Uppercase is
lowercased. A feed holds at most 500 lines (adding more drops the oldest), and
a line is at most 300 characters.

### Example: everyone's hexcoin balance

Replace everything in one call, sorted richest first and showing the top 20:

```bash
curl -X POST http://localhost:4747/ticker/api/feed/balances \
  -H "Content-Type: application/json" \
  -d '{"label":"💰 HEXBANK","template":"{key} {value}","sort":"value_desc","limit":20,
       "values":{"alice":1300,"bob":250,"carol":9001}}'
```

Then update one balance as it changes; the line updates in place:

```
GET /ticker/api/feed/balances/set?key=alice&value=1450
```

When someone leaves the bank:

```
GET /ticker/api/feed/balances/remove?key=bob
```

## Writing the JSON file directly

Feeds live in `config/ticker_feeds.json`. The server checks it every second,
and if something else changed it, reloads it and pushes the change to the
overlay. The shape is `{"feeds": [{"name": "...", "label": "...", "items":
[...]}]}`, and items can be plain strings. If the file isn't valid JSON, the
current feeds are kept and a warning is logged.

## Pulling from a URL

Give a feed a `source` and Hexcast polls it every `interval` seconds (minimum
5), replacing the feed's lines with the result:

```json
{"source": {"url": "https://example.com/balances.json", "interval": 30,
            "path": "data.users", "key_field": "name", "value_field": "coins"}}
```

The response can be:

- **A list of strings.** Each string is a line.
- **A list of objects.** `key_field` and `value_field` pick the fields. If you
  leave them blank, Hexcast looks for `key`/`name`/`user`/`username`/`login`
  and `value`/`balance`/`amount`/`coins`/`score`. `text_field` uses one field
  as the whole line.
- **A `{"key": value}` object.** For example `{"alice": 1300, "bob": 250}`.
- **Plain text.** Each non-empty line is a line.

`path` walks into the JSON with dots (for example `data.users`, or `list.0`).
A URL that starts with `/` is called on this Hexcast server directly. The
panel shows the last poll's result or error.

## Style

Everything on the panel applies live, with no Save button for the look:

- **Scrolling:** speed (px/sec on the 1920×1080 stage), direction, the gap
  between lines, the separator and its color, feed label badges and their
  colors, a pinned **title** at the left edge (for example `HEXBANK`), and
  hiding when there's nothing to show.
- **Text:** any Google Font, size, weight, letter spacing, UPPERCASE, color,
  outline and shadow.
- **Bar:** none, solid, gradient or glass background; bar color and **bar
  opacity** (how see-through the background is); border; corner radius;
  padding; soft **fade edges**; and **ticker opacity** (the whole ticker,
  text included).
- **Placement:** drag or resize the box over the live preview, or use the
  presets (bottom strip, top strip, lower third, corner box).

The panel's preview doesn't count as a connected overlay.
