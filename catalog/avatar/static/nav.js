/* Avatars - top-bar dot (see docs/plugins.md). status = /avatar/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['avatar'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Avatars - not responding' };
  if (!s.runtime) return { on: false, warn: true, title: 'Avatars - the Live2D runtime is not installed yet (open the tab)' };
  var n = +s.overlays || 0, a = (s.avatars || []).length;
  return { on: n > 0, warn: n === 0,
           title: 'Avatars - ' + a + ' avatar' + (a === 1 ? '' : 's') + ' - ' +
                  (n > 0 ? n + ' OBS overlay' + (n === 1 ? '' : 's') + ' connected' : 'no OBS overlay connected') };
};
