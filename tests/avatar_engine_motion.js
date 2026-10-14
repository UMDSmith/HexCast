/* Test helper (tests/test_avatar.py): motions started over one another, and lipsync switched back on mid-line. Runs the real
 * avatar_engine.js under node: a fake motion manager shaped like the Live2D engine's (a motion of the same or lower priority
 * than the one playing is refused unless FORCE; the one already playing always is), and a fake line of speech. Prints JSON:
 *   node avatar_engine_motion.js <path to avatar_engine.js> */
'use strict';
const { load, bare } = require('./avatar_engine_harness.js');

const file = process.argv[2];
const out = {};
const tick = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  // motion_start / motion_end, and which priority a motion asks for
  {
    const { HA } = load(file);
    const av = bare(HA), events = [], pris = [];
    av.stage.emit = (kind, e) => { if (kind === 'event') events.push([e.event, e.id, e.reason || '']); };
    const state = { currentGroup: undefined, currentIndex: undefined, currentPriority: 0 };
    av.meta = { motions: [{ name: 'wave', group: 'wave', index: 0 }, { name: 'nod', group: 'nod', index: 0 }] };
    av.spec = { FileReferences: { Motions: {} } };
    av.model = {
      internalModel: { motionManager: { state, stopAllMotions() { Object.assign(state, { currentGroup: undefined, currentIndex: undefined, currentPriority: 0 }); } } },
      motion(group, index, pri) {
        pris.push(pri);
        if (group === state.currentGroup && index === state.currentIndex) return Promise.resolve(false);       // "already playing"
        if (pri < 3 && pri <= state.currentPriority) return Promise.resolve(false);
        Object.assign(state, { currentGroup: group, currentIndex: index, currentPriority: pri });
        return Promise.resolve(true);
      },
    };
    const step = (label) => { av._trackMotion(); out[label] = events.splice(0); };
    av.command({ cmd: 'motion', name: 'wave', id: 'w1', loop: true });
    await tick(); step('started');
    av._trackMotion(); av._trackMotion();
    step('looping');                                        // a looping motion does not end by itself
    av.command({ cmd: 'motion', name: 'nod', id: 'n1' });
    await tick(); step('replaced');
    av.command({ cmd: 'motion', name: 'wave', id: 'w2', priority: 'normal' });
    await tick(); step('waited');                           // "normal" waits its turn: skipped
    av.command({ cmd: 'motion', name: 'nod', id: 'n2' });
    await tick(); step('again');                            // the one already playing: nothing
    Object.assign(state, { currentGroup: undefined, currentIndex: undefined, currentPriority: 0 });
    step('finished');
    Object.assign(state, { currentGroup: 'idle', currentIndex: 0, currentPriority: 1 });
    step('idle');                                           // the idle animation coming back is not an API motion
    av.command({ cmd: 'motion', name: 'wave', id: 'w3' });
    await tick();
    av.command({ cmd: 'stop_motion' });
    step('stopped');
    out.priorities = pris;
  }
  // lipsync switched off and back on in the middle of a line
  {
    const { HA } = load(file);
    const av = bare(HA), att = [];
    const cfg = (mouth) => ({ name: 'tester', x: 50, y: 50, scale: 1, rotation: 0, flip: false, visible: true, idle: {}, mouth });
    av.setConfig(cfg({ lipsync: true }));
    av.lip.attachAnalyser = (n) => att.push(n ? n.name : null);
    av.speech.cur = { nodes: [{ name: 'analyser' }, {}, {}] };
    av.setConfig(cfg({ lipsync: false }));
    av.setConfig(cfg({ lipsync: true }));
    av.setConfig(cfg({ lipsync: true, gain: 2 }));           // already on: left alone
    av.speech.cur = { nodes: null };                         // a line still loading: it attaches itself when it plays
    av.setConfig(cfg({ lipsync: false }));
    av.setConfig(cfg({ lipsync: true }));
    out.lipsync = att;
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
