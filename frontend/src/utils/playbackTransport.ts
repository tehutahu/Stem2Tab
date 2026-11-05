type Mode = "stopped" | "paused" | "playing";
type Phase = "pause" | "seek" | "play" | "stop";
export interface TransportPosition { currentTime: number; currentTick: number; endTime: number; isSeek?: boolean }
export interface TransportSnapshot { mode: Mode; time: number; tick: number; end: number; pending: boolean }
interface Driver { play(): boolean; pause(): void; stop(): void; seek(time: number): void }

/** Serialize untagged SDK commands. UI intent is newer than every pending reply.
 * alphaTab seeks to the beat start on pause; a matching seek acknowledgement
 * is the barrier before either showing a position or issuing the next command.
 */
export class PlaybackTransport {
  private value: TransportSnapshot = { mode: "stopped", time: 0, tick: 0, end: 0, pending: false };
  private engine: Mode = "stopped";
  private generation = 0;
  private active: { phase: Phase; generation: number; target: number } | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private closed = false;
  constructor(private driver: Driver, private publish: (state: TransportSnapshot) => void, private fail: (cause: unknown) => void) {}
  get snapshot(): TransportSnapshot { return { ...this.value }; }
  private emit(): void { if (!this.closed) this.publish(this.snapshot); }
  private clear(): void { clearTimeout(this.timer); this.timer = undefined; this.active = null; }
  dispose(): void { this.closed = true; this.clear(); }
  private error(cause: unknown): void { this.dispose(); this.fail(cause); }
  request(action: "play" | "pause" | "stop" | "seek", time?: number): void {
    if (this.closed) return;
    if (action === "play" && this.value.mode === "playing") return;
    this.generation++;
    if (action === "play") this.value.mode = "playing";
    if (action === "pause") this.value.mode = "paused";
    if (action === "stop") { this.value.mode = "stopped"; this.value.time = 0; this.value.tick = 0; }
    if (action === "seek") {
      if (!Number.isFinite(time) || time! < 0 || time! > this.value.end) { this.error(new Error("Invalid playback seek")); return; }
      this.value.time = time!;
      // The cursor waits for the matching SDK tick rather than inventing a tempo map.
    }
    this.value.pending = true;
    this.emit();
    if (!this.active) this.reconcile();
  }
  private issue(phase: Phase): void {
    this.active = { phase, generation: this.generation, target: this.value.time };
    this.timer = setTimeout(() => this.error(new Error(`Playback ${phase} acknowledgement timed out`)), 8000);
    try {
      if (phase === "stop") this.driver.stop();
      else if (phase === "pause") this.driver.pause();
      else if (phase === "seek") this.driver.seek(this.value.time);
      else if (!this.driver.play()) this.error(new Error("再生を開始できませんでした。"));
    } catch (cause) { this.error(cause); }
  }
  private reconcile(): void {
    if (this.closed) return;
    if (this.value.mode === "stopped" && this.value.time === 0) this.issue("stop");
    else if (this.engine === "playing") this.issue("pause");
    else this.issue("seek");
  }
  stateChanged(args: { state: number; stopped?: boolean }): void {
    if (this.closed) return;
    const active = this.active;
    if (active) {
      const matches = active.phase === "pause" ? !args.stopped && Number(args.state) === 0
        : active.phase === "play" ? !args.stopped && Number(args.state) === 1
        : active.phase === "stop" ? !!args.stopped : false;
      if (!matches) return;
      this.engine = args.stopped ? "stopped" : Number(args.state) === 1 ? "playing" : "paused";
      this.clear();
      if (active.generation !== this.generation) { this.reconcile(); return; }
      if (active.phase === "pause") { this.issue("seek"); return; }
      this.value.pending = false;
      this.emit();
      return;
    }
    // A natural end follows the final position. Ignore unrelated late state replies.
    if (args.stopped && this.value.mode === "playing" && this.value.end > 0 && this.value.time >= this.value.end - 50) {
      this.engine = "stopped";
      this.value = { ...this.value, mode: "stopped", time: 0, tick: 0, pending: false };
      this.emit();
    }
  }
  positionChanged(args: TransportPosition): void {
    if (this.closed) return;
    if (Number.isFinite(args.endTime)) this.value.end = args.endTime;
    const active = this.active;
    if (active) {
      // Non-seek audio samples and the SDK's beat-start pause seek are stale here.
      if (active.phase !== "seek" || !args.isSeek || Math.abs(args.currentTime - active.target) > 0.01) { this.emit(); return; }
      this.clear();
      if (active.generation !== this.generation) { this.reconcile(); return; }
      this.value.time = args.currentTime; this.value.tick = args.currentTick;
      if (this.value.mode === "playing") this.issue("play");
      else this.value.pending = false;
      this.emit();
      return;
    }
    // Only continuous playback may advance. Paused/stopped positions are pinned.
    if (this.value.mode === "playing" && !args.isSeek && args.currentTime >= this.value.time) {
      this.value.time = args.currentTime; this.value.tick = args.currentTick;
    }
    this.emit();
  }
}
