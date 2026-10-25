/* Chat Timers - top-bar dot (see docs/plugins.md). status = /chat_timers/api/status, or null. */
(window.HexbarStatus = window.HexbarStatus || {})['chat_timers'] = function (s) {
  if (!s) return { on: false, warn: false, title: 'Chat Timers - not responding' };
  var on = (s.timers || []).filter(function (t) { return t.on && t.text; }).length;
  if (s.problem) return { on: false, warn: true, title: 'Chat Timers - ' + s.problem };
  if (!s.on) return { on: false, warn: true, title: 'Chat Timers - switched off' };
  return { on: on > 0, warn: on === 0,
           title: 'Chat Timers - ' + (on ? on + ' message' + (on === 1 ? '' : 's') + ' on a timer' : 'no message switched on') };
};
