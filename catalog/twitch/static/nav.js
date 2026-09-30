/* Twitch - top-bar dot (see docs/plugins.md). status = /twitch/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['twitch'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Twitch - not responding' };
  var live = !!s.connected;
  // \u2014 is the em dash the top bar has always shown in these titles (kept as an escape: ASCII-only file).
  return { on: live, warn: !live,
           title: live ? (s.source === 'eventsub'
                            ? 'Twitch connected \u2014 chat and events'
                            : 'Twitch connected \u2014 chat only, not signed in')
                       : 'Twitch not connected' };
};
