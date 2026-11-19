/* Bot-Free Redeems - top-bar dot (see docs/plugins.md). status = /redeems/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['bot_free_redeems'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Bot-Free Redeems - not responding' };
  if (!s.on) return { on: false, warn: true, title: 'Bot-Free Redeems - switched off' };
  var rows = (s.triggers || []).filter(function (t) { return t.on; });
  var bad = rows.filter(function (t) { return t.problem; }).length;
  if (!rows.length) return { on: false, warn: false, title: 'Bot-Free Redeems - no sound set up yet' };
  if (bad) return { on: false, warn: true, title: 'Bot-Free Redeems - ' + bad + ' of ' + rows.length + ' cannot play (open the tab)' };
  return { on: true, warn: false, title: 'Bot-Free Redeems - ' + rows.length + ' sound' + (rows.length === 1 ? '' : 's') + ' ready' };
};
