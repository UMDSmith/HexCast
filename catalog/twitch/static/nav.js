/* Twitch - top-bar dot (see docs/plugins.md). status = /twitch/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['twitch'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Twitch - not responding' };
  // several channels: the dot is green when every channel that is switched on is connected
  var on = (s.channels || []).filter(function (c) { return c.enabled; });
  if (on.length > 1) {
    var up = on.filter(function (c) { return c.connected; }).length;
    return { on: up === on.length, warn: up !== on.length,
             title: 'Twitch — ' + up + ' of ' + on.length + ' channels connected' };
  }
  var live = !!s.connected;
  // \u2014 is the em dash the top bar has always shown in these titles (kept as an escape: ASCII-only file).
  return { on: live, warn: !live,
           title: live ? (s.source === 'eventsub'
                            ? 'Twitch connected \u2014 chat and events'
                            : 'Twitch connected \u2014 chat only, not signed in')
                       : 'Twitch not connected' };
};
