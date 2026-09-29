/* Discord - top-bar dot (see docs/plugins.md). status = /discord/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['discord'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Discord - not responding' };
  var live = !!s.connected;
  var title;
  if (live && s.channel) {
    title = 'Discord connected — ' + s.channel.name;
  } else if (live) {
    title = 'Discord connected — not in a voice channel';
  } else if (s.authed) {
    title = 'Discord desktop client is not running';
  } else {
    title = 'Discord not authorized';
  }
  return { on: live, warn: !!(s.authed && !live), title: title };
};
