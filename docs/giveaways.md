# Giveaways — prizes, a draw from chat, a show on stream

Add prizes with a picture and a description. Draw a winner from everyone in your Twitch chat right now, or only
from your followers (for at least a set time) and/or subscribers. The overlay counts down while the names
jumble, lands on the winner with confetti, and Hexcast congratulates them in chat.

It is a plugin. Add it from the **+** tab. The Twitch connection it uses (`twitch_core`, the same sign-in and
channels as the Twitch plugin) comes with it, so it works with or without the Twitch plugin installed.

---

## Setup

1. Open the **Giveaways** tab. Set up the **Twitch connection** box at the top if it is not set up yet (the same
   box as on the Twitch tab, see [twitch.md](twitch.md#first-run)).
2. The account needs `moderator:read:chatters` to see who is in chat. An account signed in before Giveaways
   existed is listed as missing it: press **Connect a Twitch account** again as that user.
   It also needs `user:write:chat` for the chat message. Subscriber draws need you signed in as the channel's own
   account: Twitch shows subscribers to nobody else.
3. Add the overlay to OBS as a **Browser source**: `http://localhost:4747/giveaways/overlay`, 1920×1080, above
   your other sources. It is transparent while nothing is on.
4. **+ Add a prize**: a name, a description, **How many**, and a picture (click the square: pick one you uploaded,
   upload one, or paste a link). **Save giveaway settings**.

## Who can win

Tick the groups that may win. Any ticked group counts:

- **Everyone in chat**
- **Followers**, optionally only those who followed at least N minutes / hours / days ago
- **Subscribers**

"In chat" is Twitch's own list of who is connected to your chat: lurkers too, but not people watching with
chat closed or logged out, and it can be a minute or two behind. **Who can win right now?** shows how many are
in chat and how many are left after the exclusions (and how many subscribe).

Every viewer who qualifies has the same chance. Hexcast shuffles who is in chat and takes the first one who
qualifies. Followers are looked up one at a time in that order, so a big chat costs a few lookups, not
thousands. If none of the first 500 names drawn follows long enough, it stops and says so.

Never winning: you (the broadcaster), Hexcast's own accounts, the names in **Never win** (common bots by default),
and, with **Someone in the winners list cannot win again** ticked, everyone already in the winners list (until
you clear it).

**From the chat of**: the primary channel, or another channel Hexcast watches.

## The show

**Draw a winner** (in a prize's card, or in the **On stream** box with the prize picked):

1. Hexcast picks the winner straight away, plays **Sound when the draw starts** (any soundboard clip, a
   drumroll say), and the overlay shows the prize with a reel of names under it and a countdown ring.
2. After **Countdown** seconds the reel lands on the winner. A **WINNER** badge and confetti appear, and
   **Sound when the winner shows** plays.
3. The winner goes in the **Winners** list, the prize's **How many** goes down by one, and if the prize has keys or
   codes, the first one goes to the winner (shown in the list and the **On stream** box with a Copy button).
4. After **After (seconds)** (your stream delay, 5 by default, so chat does not spoil it for viewers watching a
   few seconds behind) Hexcast posts the chat message: `{winner}`, `{login}`, `{prize}` and `{channel}` are
   filled in.
5. After **Winner stays on screen** seconds the overlay clears (0 = it stays until **Hide**).

**Reroll** draws again for that prize without the last winner (or press Reroll on an earlier row of the winners
list). The winner it replaces gives the prize and their code back when the new one shows. A reroll works even
with none of the prize left. **Cancel** stops a draw that is still counting down: nobody wins. **Show the prize**
puts the prize card on the overlay ("Up for grabs") until a draw or **Hide**.

**Test in the preview** plays the whole show with made-up names in the tab's preview only. **Test on stream**
plays it on the real overlay. Neither draws from chat, records anything or posts in chat. The preview also shows
**Look** changes before you save them.

Codes never reach the overlay or `GET /giveaways/api/status`. They are only in the tab (and
`GET /giveaways/api/config`).

## Look

Title, two accent colours (the turning border, the glow, the winner's name), text colour, a title and a text
font (any Google Fonts family; they need internet in OBS), where the card sits (centre, top, bottom, left, right),
its size, confetti on or off, and whether the description shows.

## For bots

Everything the tab does is a REST call, and every one of them is also a socket command at `/giveaways/ws`:

```text
GET  /giveaways/api/status              show.state (idle | showing | drawing | revealed), prizes (no codes), latest winners, problem
POST /giveaways/api/draw                {"prize": id, "channel"?, "eligible"?: {everyone, followers, follow_seconds, subs}}
POST /giveaways/api/reroll              {"entry"?: id}
POST /giveaways/api/cancel | /hide
POST /giveaways/api/show                {"prize": id}
POST /giveaways/api/test                {"prize"?: id, "where": "stream" | "preview"}
GET  /giveaways/api/pool                in_chat, pool (after the exclusions), subs
POST /giveaways/api/prizes              add: {name, description?, image?, quantity?, codes?}
POST /giveaways/api/prizes/{id}         change only the fields you send;  /prizes/{id}/delete
GET  /giveaways/api/config | POST       every setting and the prizes (with codes)
GET  /giveaways/api/images | POST       the picture library (POST a file);  /images/delete {"name"}
POST /giveaways/api/history/clear
```

`eligible` on a draw is for that draw only. The settings stay as they are. A draw answers with the winner at once;
the overlay reveals them after the countdown. A bot that wants to congratulate the winner itself listens on the
socket for `{"type": "event", "event": "winner", "winner": {id, login, name}, "prize": {...}, "channel": ...}`,
which arrives at the reveal (and can turn **Congratulate the winner in chat** off).

Socket commands: `status`, `config`, `set_config`, `add_prize`, `update_prize`, `delete_prize`, `show`, `hide`,
`draw`, `reroll`, `cancel`, `test`, `pool`, `clear_history`, e.g. `{"cmd": "draw", "prize": "a1b2c3d4", "rid": 1}`.
Replies carry your `rid`.

Settings, prizes and the winners list live in `config/giveaways.json`, pictures in `media/giveaways/`.
