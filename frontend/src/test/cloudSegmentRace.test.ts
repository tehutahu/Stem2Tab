/** Independent cloud regression: track switches while audio resume is pending. */
import { expect, it, vi } from "vitest";
import { SegmentPlayer } from "../utils/segmentPlayer";

function setup(includeDecay = false) {
  const nodes: any[] = [];
  const context: any = {
    currentTime: 0, state: "running", destination: {}, resume: vi.fn(async () => {}),
    createBufferSource: () => {
      const node: any = { connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn(), onended: null };
      nodes.push(node); return node;
    },
  };
  const buffers: any = { original: { duration: 10 }, bass: { duration: 10 }, synth: { duration: 10 } };
  if (includeDecay) buffers["synth-decay"] = { duration: 10 };
  const notify = vi.fn();
  const player = new SegmentPlayer(context, buffers, 10, notify);
  player.configure(1, 4, false);
  return { context, buffers, nodes, player, notify };
}

it("keeps playing the last selected source after a delayed intermediate switch", async () => {
  const { player, context, buffers, nodes } = setup();
  await player.play();
  context.currentTime = 1;
  let resolveResume!: () => void;
  context.resume.mockImplementationOnce(() => new Promise<void>(resolve => { resolveResume = resolve; }));
  const pendingBass = player.select("bass");
  await player.select("synth");
  resolveResume();
  await pendingBass;
  expect(player.snapshot()).toMatchObject({ track: "synth", playing: true, position: 2 });
  expect(nodes).toHaveLength(2);
  expect(nodes.at(-1).buffer).toBe(buffers.synth);
  expect(nodes.at(-1).start).toHaveBeenCalledWith(0, 2);
  player.dispose();
});

it("keeps the latest switch when older resumes and end events arrive out of order", async () => {
  const { player, context, buffers, nodes } = setup();
  await player.play();
  const oldEnd = nodes[0].onended;
  context.currentTime = 0.5;
  const resolutions: Array<() => void> = [];
  context.resume.mockImplementation(() => new Promise<void>(resolve => { resolutions.push(resolve); }));
  const bass = player.select("bass");
  const original = player.select("original");
  const synth = player.select("synth");
  resolutions[2](); await synth;
  resolutions[0](); resolutions[1](); await Promise.all([bass, original]);
  oldEnd();
  expect(nodes).toHaveLength(2);
  expect(nodes[1].buffer).toBe(buffers.synth);
  expect(nodes[1].start).toHaveBeenCalledWith(0, 1.5);
  expect(player.snapshot()).toMatchObject({ track: "synth", playing: true, position: 1.5 });
  player.dispose();
});

it.each(["pause", "stop", "seek", "configure", "dispose"] as const)(
  "does not resume a pending switch after %s", async action => {
    const { player, context, nodes, notify } = setup();
    await player.play(); context.currentTime = 1;
    let resolveResume!: () => void;
    context.resume.mockImplementationOnce(() => new Promise<void>(resolve => { resolveResume = resolve; }));
    const pending = player.select("bass");
    if (action === "seek") player.seek(3);
    else if (action === "configure") player.configure(0, 3, false);
    else player[action]();
    const callsAfterAction = notify.mock.calls.length;
    resolveResume(); await pending;
    expect(nodes).toHaveLength(1);
    expect(player.snapshot().playing).toBe(false);
    expect(notify).toHaveBeenCalledTimes(callsAfterAction);
    if (action === "pause") expect(player.snapshot().position).toBe(2);
    if (action === "stop") expect(player.snapshot().position).toBe(1);
    if (action === "seek") expect(player.snapshot().position).toBe(3);
    if (action !== "dispose") player.dispose();
  },
);

it("disposes an old input without allowing its late resume to start or notify", async () => {
  const old = setup(); await old.player.play();
  let resolveResume!: () => void;
  old.context.resume.mockImplementationOnce(() => new Promise<void>(resolve => { resolveResume = resolve; }));
  const pending = old.player.select("bass");
  old.player.dispose();
  const next = setup(); await next.player.play();
  const oldCalls = old.notify.mock.calls.length;
  resolveResume(); await pending;
  expect(old.nodes).toHaveLength(1);
  expect(old.notify).toHaveBeenCalledTimes(oldCalls);
  expect(next.player.snapshot()).toMatchObject({ track: "original", playing: true });
  next.player.dispose();
});

it("keeps a paused player paused when selecting repeatedly", async () => {
  const { player, nodes } = setup();
  await player.select("bass"); await player.select("synth");
  expect(nodes).toHaveLength(0);
  expect(player.snapshot()).toMatchObject({ track: "synth", playing: false });
  player.dispose();
});

it("refuses unavailable decay without interrupting legacy playback", async () => {
  const { player, nodes } = setup();
  await player.play();
  await expect(player.select("synth-decay")).rejects.toThrow("音源はありません");
  expect(player.snapshot()).toMatchObject({ track: "original", playing: true });
  expect(nodes).toHaveLength(1); expect(nodes[0].stop).toHaveBeenCalledTimes(1);
  player.dispose();
});

it("configures old packets when the optional decay buffer is explicitly undefined", () => {
  const { player, buffers } = setup();
  buffers["synth-decay"] = undefined;
  expect(() => player.configure(1, 2, false)).not.toThrow();
  expect(player.snapshot()).toMatchObject({ start: 1, end: 2 });
  player.dispose();
});

it("keeps the last decay selection at the same offset despite stale resumes and end events", async () => {
  const { player, context, nodes, buffers } = setup(true);
  await player.play(); const staleEnd = nodes[0].onended; context.currentTime = .75;
  const resolutions: Array<() => void> = [];
  context.resume.mockImplementation(() => new Promise<void>(resolve => { resolutions.push(resolve); }));
  const decay = player.select("synth-decay"), old = player.select("synth"), last = player.select("synth-decay");
  resolutions[2](); await last; resolutions[1](); resolutions[0](); await Promise.all([decay, old]); staleEnd();
  expect(nodes).toHaveLength(2); expect(nodes[1].buffer).toBe(buffers["synth-decay"]);
  expect(nodes[1].start).toHaveBeenCalledWith(0, 1.75);
  expect(player.snapshot()).toMatchObject({ track: "synth-decay", playing: true, position: 1.75 });
  player.dispose();
});

it.each(["pause", "stop", "seek", "configure", "dispose"] as const)(
  "does not revive a pending decay selection after %s", async action => {
    const { player, context, nodes, notify } = setup(true);
    await player.play(); context.currentTime = .5;
    let finish!: () => void;
    context.resume.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
    const pending = player.select("synth-decay");
    if (action === "seek") player.seek(2);
    else if (action === "configure") player.configure(0, 3, false);
    else player[action]();
    const calls = notify.mock.calls.length; finish(); await pending;
    expect(player.snapshot().playing).toBe(false); expect(nodes).toHaveLength(1);
    expect(notify).toHaveBeenCalledTimes(calls);
    if (action !== "dispose") player.dispose();
  },
);

it("resumes decay at the saved sample offset and ends without extending the interval", async () => {
  const { player, context, nodes } = setup(true);
  await player.select("synth-decay"); await player.play(); context.currentTime = .8; player.pause();
  context.currentTime = 2; await player.play();
  expect(nodes[1].start).toHaveBeenCalledWith(0, 1.8);
  expect(nodes[1].stop).toHaveBeenCalledWith(4.2);
  context.currentTime = 4.2; nodes[1].onended();
  expect(player.snapshot()).toMatchObject({ playing: false, track: "synth-decay", position: 4 });
  await player.play(); expect(nodes[2].start).toHaveBeenCalledWith(0, 1);
  player.dispose();
});
