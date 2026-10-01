# Plugins

Hexcast is two things: the **soundboard** (the core, always there) and **plugins** - everything else. Twitch, Music, Discord, Clips, Countdown, Games and Ticker are all plugins. A fresh install has one tab, **Soundboard**, and a **+** tab next to it. Click **+**, pick what you want, and it installs itself.

Games goes one level further: the **Games** plugin gives you a Games tab, and each game (Roulette, Craps, Russian Roulette, Trivia, Hexfall, Soul Climb, Blackjack) is its own add-on, installed from the **+** inside the Games tab.

- [Using plugins](#using-plugins)
- [Where things live](#where-things-live)
- [How it works](#how-it-works)
- [Writing a plugin](#writing-a-plugin)
- [plugin.json reference](#pluginjson-reference)
- [What `setup(ctx)` gets](#what-setupctx-gets)
- [Add-ons (plugins of a plugin)](#add-ons-plugins-of-a-plugin)
- [The top-bar dot and the help page](#the-top-bar-dot-and-the-help-page)
- [Versions and updates](#versions-and-updates)
- [Releasing a module update](#releasing-a-module-update)
- [Catalogs](#catalogs)
- [Upgrading from a Hexcast without plugins](#upgrading-from-a-hexcast-without-plugins)
- [Troubleshooting](#troubleshooting)

## Using plugins

**In the browser.** Open the control panel and click **+**. Every plugin has a card:

| Button | What it does |
| --- | --- |
| **Install** | Copies the plugin in, installs its Python packages if it needs any (the card shows the progress), and starts it. Nothing to restart. If it needs another plugin, that one is installed with it. |
| **Open** | Goes to the plugin's tab. |
| **Update to 1.1** | Shown when a newer version exists - in the local `catalog/` folder (after a `git pull`) or on GitHub (see [Versions and updates](#versions-and-updates); no git needed). The new files and their Python packages are prepared first while the plugin keeps running; it is stopped only for the swap and started again. Your settings are kept. If anything goes wrong the old version stays as it was. |
| **Disable / Enable** | Stops the plugin and hides its tab without deleting anything. |
| **Remove** | Deletes the plugin's files (the confirmation names anything that goes with it). **Your settings and secrets in `config/` are kept**, so installing it again brings everything back. A plugin somebody wrote or copied in by hand has no other copy, so its folder is moved to `plugins/.removed/` instead of being deleted. |
| **Repair** | Re-installs the plugin's Python packages (shown when they went missing). |

**From a terminal** (same thing, no browser - handy for scripts and Docker builds):

```bash
python hexcast.py plugins list                # installed vs latest version, and where the newer one comes from
python hexcast.py plugins install twitch music
python hexcast.py plugins install games games_roulette games_craps
python hexcast.py plugins install --all
python hexcast.py plugins update --all        # or: update twitch games_craps   (checks GitHub too, unless turned off)
python hexcast.py plugins repair music
python hexcast.py plugins remove clips        # add --with-dependents if others need it
```

A running Hexcast notices command-line changes after a restart. The **+** tab does the same work live.

## Where things live

```
hexcast.py            the soundboard + the plugin host
hexcast_core/         the plugin system itself
static/               the soundboard's own web files, the top bar, the + store page
catalog/<id>/         every plugin that ships with Hexcast (what the + tab installs from)
plugins/<id>/         installed plugins - a copy of the catalog folder; this is what runs
config/               settings and secrets, for the core and every plugin (never committed); plugins.json is the plugin host's own
VERSION               the Hexcast (core) version shown in the top bar
media/                your sounds and videos
```

Installing **copies** `catalog/<id>/` to `plugins/<id>/` (the folders `.stage-*`, `.swap`, `.trash` and `.removed` inside `plugins/` are Hexcast's own housekeeping). A `git pull` updates the catalog but never changes what is running - you press **Update** when you want it. Everything inside `plugins/` and `config/` is git-ignored.

Every plugin's `static/` folder is served at `/plugins/<id>/static/...`.

## How it works

```
 catalog/twitch/  ──Install──▶  plugins/twitch/  ──import──▶  hexcast_plugins.twitch.plugin.setup(ctx)
 (ships with Hexcast)           (the copy that runs)                   │ adds routes / websockets / static
                                                                       ▼
 hexcast.py  ── PluginHost ──▶  records exactly what setup added to the FastAPI app
   (soundboard)     │
                    ├─ Disable / Update / Remove ─▶ teardown(ctx), close its websockets, drop its routes,
                    │                               forget its modules - the rest of Hexcast never stops
                    └─ boot: scan plugins/, start each enabled plugin (dependencies first);
                       a broken one is reported on its card and skipped
```

- **The top bar is data, not code.** `GET /api/plugins/nav` lists the tabs of the installed plugins; each plugin's own
  `nav.js` decides its dot. `/help` is assembled the same way: an index plus one page (`/help/<page>`) per running plugin, built from its `help.html`.
- **Installing is copying.** The catalog folder is never imported, only copied; `plugins/<id>/.hexcast-plugin.json` remembers
  where a copy came from, its version and a digest of its files, which is how a card knows an **Update** is available (a newer
  version in `catalog/` or on GitHub, or a `catalog/` folder that changed, puts a dot on the **+** tab - nothing changes on a
  running stream until you press Update).
- **Packages are the plugin's business.** `requirements.txt` is checked (cheaply) at start and installed with pip on Install or
  Repair; in a fresh virtualenv the missing ones are restored in the background.
- **Add-ons import their parent** (`from hexcast_plugins.games import core`) and register with it; the parent's routes, pages and
  API index only ever mention add-ons that are installed.

## Writing a plugin

A plugin is a folder with two files that matter:

```
myplugin/
  plugin.json     what it is
  plugin.py       def setup(ctx): ...
  static/         optional - served at /plugins/myplugin/static/
  requirements.txt  optional - installed with pip when the plugin is
  help.html       optional - the plugin's page in /help
```

`plugin.py`:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/hello")

@router.get("")
async def hello():
    return {"hello": "world"}

def setup(ctx):
    ctx.include_router(router)      # or ctx.app.include_router(router), add_api_route, mount, websockets ...
```

`plugin.json`:

```json
{
  "id": "myplugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "Says hello.",
  "nav": { "label": "Hello", "href": "/hello" }
}
```

Drop the folder into `plugins/` and restart - or into `catalog/` and press **Install** in the **+** tab. Whatever routes, websockets and static mounts `setup` adds are tracked by the host, so **disable, update and remove undo them without any code from you**. Other changes to the app - middleware, exception handlers - are not tracked (and cannot be added once Hexcast is serving), so a plugin should not make them. If you start background tasks or open sockets, close them in an optional `teardown(ctx)` (plain or `async`) or with `ctx.on_shutdown(fn)`; if `setup` itself raises half-way, the `on_shutdown` hooks registered so far still run (newest first), so a failed start leaves nothing behind.

A plugin that fails to start (bad import, exception in `setup`) is reported on its card and in the console; it never takes Hexcast down.

## plugin.json reference

| Field | | |
| --- | --- | --- |
| `id` | required | 2-32 lowercase letters, digits, `_`. Must be the folder name. |
| `name`, `version` | required | Shown on the card and in the top bar (`1.0.0` is shown as `1.0`). A newer `version` in the catalog or upstream offers an update - **bump it whenever you change the module**. |
| `api` | | Plugin API version this was written for. Currently `1`. |
| `description`, `icon`, `color`, `category`, `author`, `homepage` | | Card details. `color` is a hex colour; `icon` an emoji. |
| `parent` | | Another plugin's id: this is an [add-on](#add-ons-plugins-of-a-plugin) of it. |
| `requires` | | Plugin ids installed together with this one. |
| `recommends` | | Plugin ids the card suggests but never forces. |
| `hidden` | | `true` = a library: no tab, not listed until something requires it. |
| `entry` | | Python module with `setup(ctx)`. Default `plugin`. |
| `requirements` | | pip requirements file. Default `requirements.txt` when the file exists. |
| `nav` | | The top-bar tab: `label`, `href`, `order`, `color`, `key`, `status_url`, `status_js`. Leave it out for a plugin without a tab. |
| `help` | | `{ "file": "help.html", "toc": [{"id": "hello", "label": "Hello"}] }` |
| `legacy_config` | | `config/` file names that show the plugin was already in use before Hexcast had plugins - see [Upgrading](#upgrading-from-a-hexcast-without-plugins). |
| `order` | | Sort position among its siblings (lower first). |

## What `setup(ctx)` gets

| | |
| --- | --- |
| `ctx.app` | the FastAPI app |
| `ctx.include_router(router)` | shorthand for `ctx.app.include_router` |
| `ctx.port` | the port Hexcast listens on |
| `ctx.id`, `ctx.manifest` | who you are |
| `ctx.plugin_dir` | your folder |
| `ctx.static_dir`, `ctx.static_url` | your `static/` folder and where it is served |
| `ctx.config_dir` | the shared `config/` folder - keep your files in it, prefixed with your id |
| `ctx.media_dir` | the media library |
| `ctx.root` | the Hexcast folder |
| `ctx.log` | a logger (`hexcast.plugin.<id>`) |
| `ctx.plugin(id)` | the entry module of another *running* plugin, or `None` (for optional friends) |
| `ctx.require(id)` | the same, but raises if it is not running |
| `ctx.on_shutdown(fn)` | run `fn` (plain or `async`) when the plugin stops |
| `ctx.changed()` | tell open pages that what you contribute changed |

Plugins are imported as `hexcast_plugins.<id>`, so a plugin can import its own modules relatively (`from . import mymodule`) and another plugin it `requires` by name (`from hexcast_plugins.ytdlp import lib`). What your entry module exposes is your plugin's Python API for others.

To play a soundboard clip, call the core over HTTP, exactly as a chat bot would - in-process, no network:

```python
import httpx
async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="http://hexcast.internal") as c:
    await c.get("/api/play/airhorn")
```

## Add-ons (plugins of a plugin)

Set `"parent": "games"` and the plugin is an **add-on** of Games: its card shows up in the **+** inside the Games tab, not in the main one. Installing an add-on installs its parent first; removing the parent removes its add-ons; the parent lists only the add-ons that are installed. Any plugin can be a parent - the parent decides what an add-on is. Games is the one that ships with Hexcast.

### A game for Games

Roulette, Craps, Russian Roulette, Trivia, Hexfall, Soul Climb and Blackjack are each an add-on of Games. To write another one:

```
games_bingo/
  plugin.json       "parent": "games"
  plugin.py         registers the game
  bingo.py          the game: a class on hexcast_plugins.games.core (Game, TableGame or RoundGame) + its routes
  static/
    bingo_panel.js  the game's tab in the Games page
    bingo.js        the game on the OBS overlay
```

`plugin.py`:

```python
from hexcast_plugins.games import core
from . import bingo

def setup(ctx):
    core.register_game(bingo.BINGO, {
        "plugin": ctx.id,
        "title": "Bingo", "order": 50,                          # tab label and position (Roulette 10, Craps 20 ...)
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "bingo_panel.js",                           # builds the tab
        "overlay": bingo.OVERLAY,                               # {"script", "appearance", "defaults", "stateful"}
        "api": bingo.API_DOC,                                   # its part of GET /games/api
    })
    ctx.include_router(bingo.router)                            # /games/api/bingo/...
    ctx.on_shutdown(lambda: core.unregister_game("bingo"))      # stops it, drops its tab and renderer
```

While it is registered the game has its own section in `config/games.json` (a removed game keeps its settings for when it comes back), its coins go through the shared ledger, and open pages learn about it at once: `GET /games/api/registry` lists the installed games and a `{"type": "registry"}` message on the Games websocket says "read it again". Nothing is reloaded by hand - the tab appears, or goes, in the page that is open.

**The overlay script** (`overlay.script`) is loaded by the OBS overlay (`/games/overlay`, or `?game=bingo` for one game) and registers a renderer, `window.HexGames.bingo`, with `create`, `setConfig`, `resize`, `play`, `showResult`, `setHistory`, `reset` and `destroy` (plus `setTable` / `setAnnounce` if the game has them). A game with `"stateful": true` (a round game) gets its whole state through `setState()` instead. `overlay.appearance` lists the settings a single round may override, `overlay.defaults` mirrors the game's default settings for a renderer that starts before its first config arrives.

**The panel script** (`panel_js`) is loaded by the Games page right after the renderer, and builds the game's tab inside the `<section id="tab-bingo">` the page made for it. It registers the handlers it wants (all optional):

```js
window.GamePanels = window.GamePanels || {};
window.GamePanels.bingo = {
  onConfig(config)  {},   // the settings arrived or changed (all games' sections)
  onState(state)    {},   // live state of this game
  onStop(game)      {},   // a stop was broadcast
  onLedger(events)  {},   // new coin movements of this game
  onLink(up)        {},   // the connection to Hexcast went up / down
  onTab(key)        {},   // a tab was shown - yours when key === 'bingo'
  onRemove()        {},   // the game is being removed: stop timers, drop listeners and injected styles
};
```

The page gives every panel `window.GamesPage`: `api(method, path[, body])` (a JSON request: returns the body, or `null` after showing a toast), `rawReq` (the same, never toasts), `busyToast`, `toast`, `esc`, `copyText`, `loadClipList` (fills the `#clip-list` datalist), `last` (the latest config / state / link, for a panel that registers after they arrived), `registry` and `gameScript(key)`. A round game (one round at a time, chat votes) can skip most of it: `GamesPage.round.register(definition)` from `round_common.js` builds the whole tab - OBS card, live mirror, controls, players, history, ledger, settings and the placement editor - from a small description of the game; `catalog/games_russian/static/russian_panel.js` is about 240 lines. `catalog/games_craps/` is a complete example of a game that does everything itself.

Keep your CSS scoped to your section (`#tab-bingo ...`), because the page and the store share the document.

## The top-bar dot and the help page

**Status dot.** A tab with `nav.status_url` gets a coloured dot. Hexcast fetches that URL every ten seconds. To decide the colour and the tooltip, ship a script (`nav.status_js`, relative to `static/`) that registers a function:

```js
(window.HexbarStatus = window.HexbarStatus || {})['myplugin'] = function (status) {
  // status: the JSON from status_url, or null if it could not be fetched
  return { on: !!(status && status.connected),   // green
           warn: !!status && !status.connected,  // amber
           title: 'My plugin - ' + (status && status.connected ? 'connected' : 'not connected') };
};
```

The key is `nav.key` (or the plugin id). Without a script the dot is green whenever the URL answers.

**Help.** While the plugin is running its `help.html` is its own page at `/help/<page>`, listed on the help index and in the sidebar (under the parent's name for an add-on). `<page>` is the first `id` in `help.toc`, and the index card uses your manifest `description`. Write full `<section id="..." class="card">...</section>` blocks; `<h3>` headings become an "On this page" row of links, so use them to split a long reference. Link to another page with `/help/<page>`.

## Versions and updates

Every module (plugin) has a `version` in its `plugin.json`, and Hexcast itself has one in the `VERSION` file. They are all shown where you can see them:

- **Top bar, top right** - `Hexcast Version: 2.0`, and next to it the version of the module whose tab you are on (`Games Version: 1.0`, `Twitch Version: 1.0`; nothing extra on the Soundboard). `X.Y.0` is shown as `X.Y`, anything else as it is (`1.2.3`). On a narrow window the word *Version:* is dropped.
- **Games** - each game's tab shows that add-on's version at the right end of the tab row (`Craps Version: 1.0`), and each game card in the game store shows it too.
- **The + store** - every card shows its version.

**Updating, without git.** When a newer version of a module exists an **Update to 1.1** button appears: next to its version in the top bar (on that module's own tab), on the game's tab in Games, on its card in the **+** store, and as a green dot on the **+** tab (and on a game's tab in Games). Pressing it updates **that module only** - the same safe path as the store's Update button: the new files and Python packages are prepared while the old version keeps running, the module is stopped only for the swap, your settings in `config/` stay, and if anything fails the old version is left running. The page then shows the new version by itself; there is nothing to restart. Updating one game add-on (`games_craps`) touches only that add-on, not Games or the other games.

For **Hexcast itself** the top bar reads `Hexcast Version: 2.0` with an **Update to 2.1** link. It opens the repository's ZIP download: unpack it over your Hexcast folder (your `media/`, `config/` and `plugins/` are not in the ZIP and stay). Hexcast never overwrites its own core files.

**Where "newer" comes from.** Two places are compared with what is installed, and the newest wins:

1. the `catalog/` folder next to Hexcast (what `git pull` or a new ZIP refreshes). A catalog copy whose files changed without a version bump is still offered as an update.
2. **upstream**: the project's GitHub repository. Hexcast asks GitHub for the `plugin.json` of each plugin you have installed. This is what tells ZIP users that a newer module exists without having to download everything again.

An update is offered only when the version is **semver-greater** than the installed one (`1.10.0` beats `1.9.0`; `1.2.0-beta` is older than `1.2.0`); never a downgrade.

**Applying an upstream update.** Hexcast downloads the repository archive from GitHub (`https`, size-capped, streamed to a temporary file), unpacks **only** `catalog/<plugin id>/` from it (no `..`, no absolute paths, no links; nothing else in the archive is touched), and then it goes through the ordinary update path described above.

### The Settings page and the `upstream` setting

The **gear** at the top right of every page opens **Settings** (`/options`), the master settings for all of Hexcast (Updates is the first section; later global options go there too). It has:

- **Check GitHub for updates** - on by default. Off = Hexcast makes **no update requests at all** (no module check, no Hexcast-version check, nothing in the background); version numbers still show, and an update that arrives in the local `catalog/` folder (`git pull`) is still offered. Upstream updates are not offered while it is off.
- **GitHub repository** and **Branch** - where to look (default `UMDSmith/hexcast`, `main`); validated before they are saved (a pasted `https://github.com/owner/name` is accepted).
- **Check now** - asks GitHub right away; the page shows when it last asked, the source, the latest Hexcast version and which installed modules have an update (or that GitHub could not be reached).

It saves to `config/plugins.json`, which you can also edit by hand:

```json
{ "check_updates": true, "upstream": { "repo": "UMDSmith/hexcast", "branch": "main" } }
```

- `check_updates` - `true` (the default if the key is missing) or `false`.
- `upstream` - left out (or `true`, or `{}`) = the default repository; set `repo` (`owner/name`) and `branch` to follow a fork. `"upstream": false` is the older way to say the same as `"check_updates": false`.
- A value that is not usable (a bad repo name, a non-GitHub address) is ignored with a warning in the console and the default is used.
- For testing against your own local server `raw_url` and `archive_url` (templates with `{repo}`, `{branch}`, `{id}`) can be added to `upstream`; like everything here they must be `https` GitHub or `http` `localhost`.
- The environment variable `HEXCAST_NO_UPSTREAM=1` switches the check off without touching the file (Docker builds, offline machines); Settings then shows the switch as locked.

**The Options API** (what the page uses; built so future global options are one more entry in `hexcast_core/options.py`): `GET /api/options` returns `{"options": {...}, "defaults": {...}, "locked": {...}, "updates": {checked_at, last_ok_at, error, core_latest, modules}, "available": [...]}`; `POST /api/options` with e.g. `{"check_updates": false}` or `{"upstream_repo": "me/fork", "upstream_branch": "dev"}` validates every value first and saves them all or none (`400` with `errors` per key); `POST /api/options/check-now` fetches the latest versions now (`409` while checking is off). Like the plugin API it only accepts requests from Hexcast's own pages and from scripts.

**How it behaves.** The check runs in a background thread, never while a page loads or while Hexcast starts; the answer is cached for an hour (a failed attempt is retried after five minutes); offline simply means no news, with no error shown. Only `https` to `github.com`, `raw.githubusercontent.com` and `codeload.github.com` is used (also after redirects), and only for plugins you have installed that came from Hexcast.

**Privacy.** What is sent to GitHub: ordinary HTTPS GET requests for `VERSION` and for the `plugin.json` of each plugin you have installed (so GitHub can see your IP address and which module files were requested, like any download from it), plus - only when you press an update - the repository ZIP. Nothing about you, your streams or your settings is sent. Turn all of it off with the switch in Settings (the gear), or `"check_updates": false`.

**From a terminal.** `python hexcast.py plugins list` shows installed vs latest versions and where an update would come from (`catalog` or `upstream`); `plugins update <id>` / `plugins update --all` applies it (`--all` only touches plugins that have something newer).

**API.** `GET /api/plugins`, `/api/plugins/nav` and the Games registry carry `installed_version`, `latest_version` (and `installed_label` / `latest_label`, the display form), `update_available` and `update_source` (`"catalog"` or `"upstream"`); the existing fields are unchanged. `GET /api/version` also returns `repo_url` and `download_url` (where Hexcast core's latest download is) and `check_enabled`.

## Releasing a module update

For the person who ships the code:

1. Change the module (`catalog/<id>/...`).
2. Bump `"version"` in `catalog/<id>/plugin.json` (`1.0.0` -> `1.1.0`). For Hexcast core, bump the `VERSION` file instead.
3. Push to `main` (the branch users follow).

Within an hour (the cache time) everybody who has the module installed sees **Update to 1.1** and can apply it with one click - no git needed. Without the version bump nobody is told, so remember step 2.

## Catalogs

The **+** tab lists the *catalog*. Two sources:

1. **Bundled** - the `catalog/` folder in the Hexcast download. Always available, works offline.
2. **Remote** (optional) - a JSON index somewhere on the web, so plugins can be shared without changing Hexcast. Add its URL to `config/plugins.json`:

   ```json
   { "catalogs": ["https://example.com/hexcast/index.json"] }
   ```

   ```json
   {
     "api": 1,
     "plugins": [
       { "manifest": { "id": "hello", "name": "Hello", "version": "1.0.0" },
         "url": "hello-1.0.0.zip",
         "sha256": "<sha-256 of the zip>" }
     ]
   }
   ```

   The zip holds the plugin folder's contents (or one top-level folder with them). The index and the downloads must be `https://` (plain `http://` only for `localhost`), and every plugin needs its `sha256`: Hexcast skips an entry without one, refuses a zip whose checksum does not match, and never unpacks anything outside `plugins/<id>/`. If the index cannot be reached the last good list stays; it is fetched again after a minute. **A plugin is code that runs on your PC with your permissions: only add catalogs you trust.** Remote catalogs can only be added by editing that file, never from the web page.

`python tools/build_catalog.py out/` builds such an index and the zips from the `catalog/` folder.

## Upgrading from a Hexcast without plugins

The first start after upgrading looks in `config/` for the settings of each bundled plugin (`legacy_config` in its manifest). Every plugin that was already in use is installed for you, so your tabs, overlays and OBS sources keep working with no clicks. A brand-new install has none of those files and starts with just the soundboard. This runs once (`"migrated": true` in `config/plugins.json`); removing a plugin later never brings it back.

## Troubleshooting

- **A card says "Python packages are missing"** - press **Repair**. This also happens by itself in the background when Hexcast starts in a fresh virtualenv.
- **A plugin will not start** - the card shows the error; `hexcast.log` has the full traceback. Fix it and press **Update** (or **Disable** then **Enable**).
- **"config/plugins.json is not valid JSON" at start** - the file was copied to `config/plugins.json.bad` and Hexcast started with the defaults (it does not run the upgrade step over it). Fix the JSON, or delete the file to start over.
- **An install failed** - nothing is left half-installed: the card is back to **Install** with the log of what went wrong (usually pip and the internet connection). An update that fails leaves the old version running.
- **Files in `plugins/.trash/`** - Windows had a file open while removing a plugin; they are deleted on the next start.
- **Plugin changes from other web pages are refused** - the plugin API only accepts requests from the pages Hexcast serves (and from scripts and `curl`, which send no `Origin`), so a site open in another tab cannot install or remove plugins.
- **No "Update to ..." appears though a newer version is on GitHub** - the check is cached for an hour and needs the internet; check **Check GitHub for updates** is on in Settings (the gear), and that the new `plugin.json` has a higher `version`.
- **Developing a plugin in `catalog/`** - start Hexcast with `HEXCAST_DEV=1` and changed plugins are re-installed from the catalog on every start.
