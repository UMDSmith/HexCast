/* Countdown - top-bar dot (see docs/plugins.md). status = /countdown/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['countdown'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Countdown - not responding' };
  var n = +s.overlays || 0, t = s.timer || {};
  var mm = Math.floor((t.remaining || 0) / 60), ss = Math.floor((t.remaining || 0) % 60);
  var clock = (mm < 10 ? '0' : '') + mm + ':' + (ss < 10 ? '0' : '') + ss;
  return { on: n > 0, warn: true,
           title: 'Countdown - ' + (t.running ? 'running, ' + clock + ' left' : 'stopped at ' + clock) + ' - ' +
                  (n > 0 ? n + ' overlay' + (n === 1 ? '' : 's') + ' connected' : 'no overlay connected') };
};
