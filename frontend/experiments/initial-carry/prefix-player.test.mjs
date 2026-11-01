/** Synthetic transport tests for the unconnected prototype, not audible quality. */
import { test } from 'vitest';
import assert from 'node:assert/strict';
import { PrefixPlayer } from './prefix-player.js';

function setup() {
  const nodes = [], notifications = [];
  const context = {currentTime:0, state:'running', destination:{}, resume:async()=>{},
    createBufferSource() {
      const node = {onended:null, starts:[], stops:[], disconnects:0,
        connect(){}, disconnect(){this.disconnects++}, start(...args){this.starts.push(args)}, stop(...args){this.stops.push(args)}};
      nodes.push(node); return node;
    }};
  const buffers = Object.fromEntries(['baseline','decay','carry','carry-decay'].map(key=>[key,{duration:4}]));
  return {context, buffers, nodes, notifications,
    player:new PrefixPlayer(context,buffers,state=>notifications.push(state))};
}

test('four prototype keys preserve position; late resumes and ended events cannot override the last choice',async()=>{
  const {player,context,buffers,nodes}=setup(); await player.play();
  const stale=nodes[0].onended; context.currentTime=1;
  const pending=[];context.resume=()=>new Promise(resolve=>pending.push(resolve));
  const a=player.select('decay'), b=player.select('carry'), c=player.select('carry-decay');
  pending[2]();await c;pending[0]();pending[1]();await Promise.all([a,b]);stale();
  assert.equal(nodes.length,2);assert.equal(nodes[1].buffer,buffers['carry-decay']);
  assert.deepEqual(nodes[1].starts,[[0,1]]);
  assert.deepEqual(player.snapshot(),{track:'carry-decay',playing:true,position:1});player.dispose();
});

for(const action of ['pause','stop','seek','dispose']) {
  test(`pending prototype playback cannot revive after ${action}`,async()=>{
    const {player,context,nodes,notifications}=setup();await player.play();context.currentTime=.5;
    let finish;context.resume=()=>new Promise(resolve=>{finish=resolve});
    const pending=player.select('carry');
    if(action==='seek')player.seek(2);else player[action]();
    const count=notifications.length;finish();await pending;
    assert.equal(nodes.length,1);assert.equal(player.snapshot().playing,false);assert.equal(notifications.length,count);
    if(action!=='dispose')player.dispose();
  });
}

test('prototype pause/seek/replay use the same sample clock and fixed four-second end',async()=>{
  const {player,context,nodes}=setup();await player.select('carry');await player.play();context.currentTime=.5;player.pause();
  context.currentTime=1;await player.play();assert.deepEqual(nodes[1].starts,[[0,.5]]);
  player.seek(3);await player.play();assert.deepEqual(nodes[2].starts,[[0,3]]);
  context.currentTime=2;nodes[2].onended();assert.equal(player.snapshot().position,4);
  await player.play();assert.deepEqual(nodes[3].starts,[[0,0]]);player.stop();assert.equal(player.snapshot().position,0);player.dispose();
});

test('missing prototype choices are explicit and do not interrupt current playback',async()=>{
  const {player,nodes}=setup();await player.play();
  await assert.rejects(player.select('missing'),/音源/);assert.equal(player.snapshot().playing,true);
  assert.equal(nodes.length,1);assert.throws(()=>player.seek(-1),/区間外/);player.dispose();
});

test('prototype resume errors remain failures',async()=>{
  const {player,context,nodes}=setup();context.resume=async()=>{throw Error('synthetic resume failure')};
  await assert.rejects(player.play(),/synthetic resume failure/);assert.equal(nodes.length,0);assert.equal(player.snapshot().playing,false);player.dispose();
});
