/* Music - top-bar dot (see docs/plugins.md). status = /ytm/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['music'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Music — not responding' };
  var live = !!s.connected;
  var title;
  if (live && s.now && s.now.title) {
    title = (s.now.playing ? '▶ ' : '⏸ ') + s.now.title + ' — ' + s.now.author;
  } else if (live) {
    title = 'YouTube Music connected — nothing playing';
  } else if (s.paired) {
    title = 'YouTube Music Desktop is not running';
  } else {
    title = 'YouTube Music not paired';
  }
  return { on: live, warn: !!(s.paired && !live), title: title };
};
