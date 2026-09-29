/* Clips - top-bar dot (see docs/plugins.md). status = /clips/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['clips'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Clips — not responding' };
  var live = !!s.ytdlp;
  var title;
  if (s.player && s.player.state !== 'idle' && s.player.item) {
    title = 'Clips — playing #' + s.player.item.num + ' ' + (s.player.item.title || '');
  } else if (live) {
    title = 'Clips — ' + s.queued + ' queued';
  } else {
    title = 'Clips — yt-dlp not installed';
  }
  return { on: live, warn: !live, title: title };
};
