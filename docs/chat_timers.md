# Chat Timers — messages in chat on a timer

Hexcast posts messages in your Twitch chat on a timer, as your account: the Discord link, the schedule, your
socials. It works like StreamElements' or Nightbot's timers. There is no overlay: everything happens in chat.

It is a plugin. Add it from the **+** tab. The Twitch connection it uses (`twitch_core`, the same sign-in and
channels as the Twitch plugin) comes with it, so it works with or without the Twitch plugin installed.

---

## Setup

1. Open the **Timers** tab. At the top is the **Twitch connection** box, the same one the Twitch and Giveaways
   tabs show. If you already set Twitch up, it says so and stays folded. Otherwise:
   create the Twitch app, paste the Client ID and secret, add your channel, then **Connect a Twitch account**
   (details in [twitch.md](twitch.md#first-run)).
2. The account needs the `user:write:chat` scope, so it can post. Hexcast asks for it at sign-in. An account
   signed in before the `!so` command existed is listed as missing it: connect it again.
3. **+ Add a message**, type it, **Save timers**.

## How a message decides to post

Each message has:

| Setting | |
| --- | --- |
| **Every (minutes)** | at least this long since it last posted (1–1440) |
| **After at least (chat lines)** | and at least this many chat lines since then (0 = no need). Lines from Hexcast's own accounts do not count, so timers cannot keep each other going |
| **Post in** | the primary channel, every channel, or one channel by name |
| **Only while live** | on by default: nothing posts while the channel is offline |
| **on** | its switch |

When the stream starts, the clock starts again: the first message comes one interval into the stream, not the
moment you go live. A new message also waits a full interval before its first post.

With several messages due at once in a channel, the one that has waited longest goes first. The next one waits
at least **Seconds between two timer messages** (60 by default), so they never arrive in a burst.

A message Twitch refuses (no scope, the account cannot post there, a duplicate message) shows the reason under
it and in the Twitch log, then waits another full interval before it tries again.

`{channel}` (the channel's name) and `{uptime}` (`1h 05m` while live, else `offline`) are filled in. Anything
else in braces stays as you typed it.

**Send now** posts the saved message right away, whether it is due or not, and starts its clock again.

## For bots

Everything the tab does is a REST call, and every one of them is also a socket command at
`/chat_timers/ws`:

```text
GET  /chat_timers/api/status                    every timer, per channel: waiting_for, next_in, lines_needed, sent, last_error
GET  /chat_timers/api/config                    {on, gap, timers}
POST /chat_timers/api/config                    {on?, gap?, timers?}  (timers replaces the whole list)
POST /chat_timers/api/timers                    add: {text, every?, lines?, live_only?, channel?, name?, on?}
POST /chat_timers/api/timers/{id}               change only the fields you send
POST /chat_timers/api/timers/{id}/send          post now ({"channel": "name"} for one channel)
POST /chat_timers/api/timers/{id}/delete
```

`waiting_for` is `interval`, `chat`, `live`, `gap` or `off`, or `""` when the message is due. `problem` in the
status says why posting cannot work at all (not signed in, a missing scope).

Socket: `{"cmd": "send", "id": "a1b2c3d4", "rid": 1}` answers `{"type": "result", "cmd": "send", "ok": true,
"rid": 1, ...}`. Commands: `status`, `config`, `set_config`, `add`, `update`, `delete`, `send`. Status updates
arrive as `{"type": "status", "status": {...}}` every few seconds.

To post a one-off line that is not a timer, use the shared connection: `POST /twitch/api/chat/send`
`{"text": "...", "channel": "name"}`.

Settings live in `config/chat_timers.json`.
