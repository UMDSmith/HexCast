# Bot-Free Redeems — Twitch redeems, commands and bits play soundboard clips

For streamers with **no bot or AI** in between: a viewer redeems a channel point reward, types a chat command like
`!airhorn`, or cheers bits, and Hexcast plays a sound from your Soundboard. It runs straight from Twitch to the
Soundboard, and everything is set up in the **Redeems** tab.

It is a plugin. Add it from the **+** tab. The Twitch connection it uses (`twitch_core`, the same sign-in and
channels as the Twitch plugin) comes with it, so it works with or without the Twitch plugin installed. The sounds
play on the Soundboard overlay (`/overlay`). Bot-Free Redeems has no overlay of its own.

---

## Setup

### 1. Put the Soundboard overlay in OBS

Every sound plays on the Soundboard overlay. The Twitch alerts and the games use the same one, so you may already
have it. If not, add a **Browser** source in OBS:

| | |
| --- | --- |
| URL | `http://localhost:4747/overlay` (OBS on another PC: the Hexcast PC's address instead of `localhost`) |
| Width × Height | 1920 × 1080 |
| Shutdown source when not visible | **off** |
| Control audio via OBS | **on**, so the sound shows in the OBS mixer and goes to your stream |

The Redeems tab shows this URL with a **Copy** button.

### 2. Add your sounds to the Soundboard

Open the **Soundboard** tab and drop in sound files (mp3, wav, ogg...) or short videos and GIFs. A clip's name (its
file name without the extension) is what you pick in the Redeems tab. Set a clip's volume, trim, position and
cooldown on the Soundboard: they apply to redeems too.

### 3. Connect Twitch

The **Twitch connection** box at the top of the Redeems tab is the same box as on the Twitch, Timers and Giveaways
tabs. If Twitch is already set up there, it says so and stays folded.

- **Chat commands and bits** need only your channel name. Add it under **Channels** and save. Hexcast reads your
  chat without signing in (anonymous chat).
- **Channel point redeems** also need a sign-in **as the channel itself**. Twitch tells only a channel's own account
  about its redeems. The first time, the box walks you through making a Twitch app (about two minutes, see
  [twitch.md](twitch.md#events-need-a-sign-in)). Then press **Connect a Twitch account** while you are signed in to
  Twitch as your channel. The scope is `channel:read:redemptions`. Hexcast asks for it at sign-in, so any account
  signed in from the start already has it.
- **Sign in on the Hexcast PC.** Twitch takes a plain `http` redirect only for `localhost`, so the sign-in always
  comes back to `http://localhost:4747` (your port), even from a tab opened as `192.168...`. From another computer it
  cannot come back unless Hexcast is served over `https`.
- Twitch has channel points only for **Affiliate and Partner** channels. Commands and bits work for everyone.

### 4. Make your rewards on Twitch (for redeems)

Make the rewards on Twitch as usual: Creator Dashboard → **Viewer Rewards** → **Channel Points** → **Manage Rewards
& Challenges** → **Add New Custom Reward**. Give each reward a name and a cost.

Set the reward's **cooldown** and its limits there too: *Limit Redemptions per Stream*, *Limit Redemptions per User
per Stream*, *Cooldown*. Twitch checks them before the points are spent, so a viewer is never charged for a sound
that is held back. Leave *Require Viewer to Enter Text* off for a plain sound.

### 5. Add the rows

In the Redeems tab press **+ Add a redeem sound**, **+ Add a command** or **+ Add a bits sound**. Fill in the row,
pick its sound and press **Save**. **▶ Test** plays the sound right away on the Soundboard overlay, so it is on
stream. Then try it for real: redeem the reward, or type the command in your chat. The **Recent** list at the bottom
shows each one.

---

## Channel point redeems

| Setting | |
| --- | --- |
| **Reward** | picked from your channel's rewards. Hexcast asks Twitch for them; press **↻ Reload rewards** after you add one on Twitch. Rewards switched off or paused on Twitch are marked |
| **Sound** | the soundboard clip it plays |

The row remembers the reward itself, so renaming the reward on Twitch keeps it working. A reward deleted and made
again with the same name is found by its name.

When Hexcast cannot list the rewards (not signed in as the channel, not an Affiliate, Twitch not answering), the
card says why and the row takes the reward's **exact name** instead. Picking **Type a name instead...** in the list
does the same.

A redeem always plays. There is no cooldown here, because the viewer has already spent the points by the time Hexcast
hears about it. Use the reward's own cooldown on Twitch.

## Chat commands

| Setting | |
| --- | --- |
| **Command** | `!airhorn`, or a few on one row: `!airhorn !horn` (up to 5). Capitals do not matter. A word typed without `!` gets one |
| **Who can use it** | everyone · subscribers, VIPs and mods · VIPs and mods · moderators only · only you (the broadcaster) |
| **Cooldown (seconds)** | after it plays, nobody can use it again for this long (30 to begin with; 0 = no cooldown) |
| **Same viewer again after (seconds)** | the viewer who used it waits this long (0 = off) |

The command has to be the **first word** of the chat line (`!airhorn lol` plays, `lol !airhorn` does not). A reply
to someone (`@name !airhorn`) counts too.

You, the broadcaster, are never held up by a cooldown, so you can test on stream. A command that is on cooldown or
not allowed does nothing in chat. The Recent list says why it did not play. A sound nobody heard (the clip is gone,
the overlay is closed) does not start the cooldown.

## Bits

| Setting | |
| --- | --- |
| **At least (bits)** | a cheer of at least this many plays the sound |

With several amounts, the biggest one the cheer reaches wins. With 100 and 1000 set, a 500-bit cheer plays the 100
sound and a 1500-bit cheer the 1000 one. A cheer smaller than every amount plays nothing. Cheers are read from the
chat, so bits need no sign-in either.

## Several sounds on one row

**+ another sound** adds more sounds to a row (up to 10). Each time the row plays, one of them is picked at random,
never the same one twice in a row. Handy for a "random fart noise" reward.

## The Recent list and the row status

The bottom of the tab lists the last 40 redeems, commands, cheers and tests, newest first: the time, who, what, and
what happened. It is **played** (and which sound), or why not:

- no sound set for this reward
- on cooldown (and how long is left), or the same viewer has to wait
- only subscribers / VIPs / moderators / the broadcaster can use it
- less than the smallest bits sound
- the clip is not on the Soundboard
- the clip is still in its Soundboard cooldown
- nobody heard it: the Soundboard overlay is not open in OBS

The Recent list is kept in memory only, so a restart clears it.

Every row also has a status line. It says **ready**, or what stops the row (no reward, command or sound picked; a
sound that is no longer on the Soundboard; a row above with the same command or reward, which wins; redeems not
reaching Hexcast and why). It also says how many times the row has played, and when and by whom last.

The dot on the **Redeems** tab in the top bar is grey when nothing is set up and amber when a row cannot play.

## The master switch

**Sounds on** switches everything off and on. Off, nothing plays (handy for a quiet stream). The Twitch alerts then
play their own clips again (see below).

## With the Twitch plugin's alerts

The Twitch tab's alerts can play one **Soundboard clip** with every redeem and one with every cheer. When a redeem
or a cheer has a sound here, only this sound plays. The alert still shows, but its own clip stays quiet for it, so
nothing plays twice. Rewards with no row here still play the alert's clip. This needs Twitch 1.3.2 or newer.
**Update** it from the **+** tab.

## Several channels

When the Twitch connection box watches more than one channel, every row gets a **Channel** setting: the primary
channel (the default), every channel, or one of them. The reward list is the primary channel's for a row on the
primary or every channel.

## Settings file

Everything you save is in `config/bot_free_redeems.json`. Uninstalling the plugin keeps it, so installing it again
brings your rows back.

---

## When a sound does not play

Look at the **Recent** list first, then the row's own status line.

**Nothing shows in Recent for a redeem.** Hexcast did not hear about it. The redeem card says why when it knows (not
signed in as the channel, Twitch refused the redeem events). Sign in as the channel's own account in the Twitch
connection box. An account signed in long ago may lack a permission: press **Connect a Twitch account** again as that
user. With the Twitch plugin installed, its **Status** tab lists every Twitch subscription and why one failed.

**Nothing shows in Recent for a command.** Check that the channel in the Twitch connection box is yours and is
connected (the command card says when chat is not connected), and that the command is the first word of the line.

**Recent says "nobody heard it".** The Soundboard overlay is not open. Check the Browser source in OBS (step 1) and
that **Shutdown source when not visible** is off.

**Recent says "played" but the stream has no sound.** The overlay played it, but OBS does not pass it on. Tick
**Control audio via OBS** on the source and check its volume in the OBS mixer (and that it is not muted).

**"no sound set for this reward".** No row matches that reward. Pick it in a row, or check the name you typed, and
press **Save**.

**"still in its Soundboard cooldown".** The clip has a cooldown of its own on the Soundboard. Lower it there, or use
the reward's cooldown on Twitch instead.

**"not on the Soundboard".** The clip was renamed or deleted on the Soundboard. Pick it again in the row.

**The reward list says Affiliate or Partner.** Twitch has channel points only for those channels. Chat commands and
bits work for everyone.

**A sound plays twice.** The Twitch tab's redeem or bits alert has a Soundboard clip too, and the Twitch plugin is
older than 1.3.2. Update it from the **+** tab, or clear the alert's clip.
