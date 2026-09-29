/* Ticker - top-bar dot (see docs/plugins.md). status = /ticker/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['ticker'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Ticker - not responding' };
  var n = +s.overlays || 0;
  var what = !s.visible ? 'hidden'
    : s.items + ' line' + (s.items === 1 ? '' : 's') + ' scrolling' +
      (s.scrolling && s.scrolling.length ? ' (' + s.scrolling.join(', ') + ')' : '');
  return { on: n > 0, warn: true,
           title: 'Ticker — ' + what + ' · ' +
                  (n > 0 ? n + ' overlay' + (n === 1 ? '' : 's') + ' connected' : 'no overlay connected') };
};
