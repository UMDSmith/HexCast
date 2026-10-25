/* Giveaways - top-bar dot (see docs/plugins.md). status = /giveaways/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['giveaways'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Giveaways - not responding' };
  var st = (s.show || {}).state, n = +s.overlays || 0;
  var now = st === 'drawing' ? 'drawing a winner' : st === 'revealed' ? 'winner on screen'
          : st === 'showing' ? 'prize on screen' : 'idle';
  if (s.problem) return { on: false, warn: true, title: 'Giveaways - ' + s.problem };
  return { on: n > 0, warn: n === 0,
           title: 'Giveaways - ' + now + ' - ' + (n ? n + ' overlay' + (n === 1 ? '' : 's') + ' connected' : 'no overlay connected') };
};
