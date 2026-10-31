/** One source at a time, sharing an explicit interval and source-seconds clock. */
export type TrackKey = "original" | "bass" | "synth";
export type SegmentState = { track: TrackKey; playing: boolean; position: number; start: number; end: number; loop: boolean };

export class SegmentPlayer {
  private node: AudioBufferSourceNode | null = null;
  private epoch = 0;
  // A track change may be waiting for AudioContext.resume after halting the old node.
  private switchPending = false;
  private disposed = false;
  private startedAt = 0;
  private startedPosition = 0;
  state: SegmentState;
  constructor(private context: AudioContext, private buffers: Record<TrackKey, AudioBuffer>, duration: number,
    private notify: (state: SegmentState) => void) {
    this.state = { track: "original", playing: false, position: 0, start: 0, end: duration, loop: false };
  }
  snapshot(): SegmentState {
    let position = this.state.position;
    if (this.state.playing) {
      const elapsed = Math.max(0, this.context.currentTime - this.startedAt);
      position = this.startedPosition + elapsed;
      position = this.state.loop ? this.state.start + ((position - this.state.start) % (this.state.end - this.state.start)) : Math.min(position, this.state.end);
    }
    return { ...this.state, position };
  }
  private emit(): void { if (!this.disposed) this.notify(this.snapshot()); }
  private halt(): void {
    this.epoch++;
    this.switchPending = false;
    if (this.node) { this.node.onended = null; this.node.stop(); this.node.disconnect(); this.node = null; }
    this.state.playing = false;
  }
  configure(start: number, end: number, loop: boolean): void {
    const duration = Math.min(...Object.values(this.buffers).map(b => b.duration));
    if (![start, end].every(Number.isFinite) || start < 0 || end > duration + 1e-6 || end - start < .05) throw new Error("区間は音源内の50ms以上を指定してください。");
    this.halt(); Object.assign(this.state, { start, end, loop, position: start }); this.emit();
  }
  async play(): Promise<void> {
    if (this.disposed) return;
    const position = this.snapshot().position;
    this.halt();
    const token = this.epoch;
    try {
      await this.context.resume();
      if (token !== this.epoch || this.disposed) return;
      if (this.context.state !== "running") throw new Error("音声再生を開始できません。");
      const offset = position >= this.state.end ? this.state.start : position;
      const node = this.context.createBufferSource(); this.node = node;
      node.buffer = this.buffers[this.state.track]; node.loop = this.state.loop;
      node.loopStart = this.state.start; node.loopEnd = this.state.end;
      node.connect(this.context.destination);
      this.startedAt = this.context.currentTime; this.startedPosition = offset;
      node.onended = () => {
        if (this.disposed || token !== this.epoch || this.node !== node) return;
        this.node = null; node.disconnect(); this.state.playing = false; this.state.position = this.state.end; this.emit();
      };
      node.start(0, offset);
      if (!this.state.loop) node.stop(this.context.currentTime + this.state.end - offset);
      this.state.playing = true; this.state.position = offset; this.emit();
    } catch (error) {
      if (token !== this.epoch || this.disposed) return;
      this.halt(); this.emit(); throw error;
    }
  }
  pause(): void { const position = this.snapshot().position; this.halt(); this.state.position = position; this.emit(); }
  stop(): void { this.halt(); this.state.position = this.state.start; this.emit(); }
  async select(track: TrackKey): Promise<void> {
    const previous = this.snapshot();
    const shouldResume = previous.playing || this.switchPending;
    this.halt(); this.state.track = track; this.state.position = previous.position; this.emit();
    if (shouldResume) {
      const pending = this.play();
      const token = this.epoch;
      this.switchPending = true;
      try { await pending; }
      finally { if (token === this.epoch) this.switchPending = false; }
    }
  }
  seek(position: number): void {
    if (!Number.isFinite(position) || position < this.state.start || position > this.state.end) throw new Error("再生位置が区間外です。");
    this.halt(); this.state.position = position; this.emit();
  }
  dispose(): void { this.halt(); this.disposed = true; }
}
