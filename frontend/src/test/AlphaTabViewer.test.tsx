import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const layout = vi.hoisted(() => ({ install: vi.fn() }));
vi.mock("../utils/readableEngraving", () => ({ installReadableEngravingLayout: layout.install }));
const mock = vi.hoisted(() => ({ apis: [] as any[] }));
function event() { const callbacks: Array<(v?: any) => void> = []; return { on: (f: (v?: any) => void) => callbacks.push(f), emit: (v?: any) => callbacks.forEach(f => f(v)) }; }
vi.mock("@coderline/alphatab", () => ({ AlphaTabApi: class {
  renderFinished = event(); scoreLoaded = event(); playerReady = event(); error = event(); playerStateChanged = event(); activeBeatsChanged = event();
  play = vi.fn(); pause = vi.fn(); stop = vi.fn(); destroy = vi.fn(); load = vi.fn(() => true);
  constructor(_element: unknown, public settings: unknown) { mock.apis.push(this); }
} }));
import AlphaTabViewer from "../components/AlphaTabViewer";
beforeEach(() => { mock.apis.length = 0; layout.install.mockReset(); });
it("uses the supported SDK settings, gates playback and maps actual paused/stopped states", () => {
  const state = vi.fn(); render(<AlphaTabViewer scoreUrl="/score.xml" onPlayerStateChange={state} />);
  const api = mock.apis[0]; expect(api.load).toHaveBeenCalledWith("/score.xml");
  expect(api.settings.core.scriptFile).toBe("/alphatab/alphaTab.min.js");
  expect(screen.getByRole("button", { name: /play/i })).toBeDisabled();
  act(() => api.playerReady.emit());
  for (const name of ["play", "pause", "stop"] as const) { fireEvent.click(screen.getByRole("button", { name: new RegExp(name, "i") })); expect(api[name]).toHaveBeenCalledOnce(); }
  act(() => { api.playerStateChanged.emit({ state: 0, stopped: false }); api.playerStateChanged.emit({ state: 0, stopped: true }); });
  expect(state.mock.calls).toEqual([["paused"], ["stopped"]]);
});
it("ignores old state, beats and readiness after score changes or unmount", () => {
  const state = vi.fn(), notes = vi.fn();
  const view = render(<AlphaTabViewer scoreUrl="/one.xml" onPlayerStateChange={state} onActiveNotesChange={notes} />);
  const old = mock.apis[0]; view.rerender(<AlphaTabViewer scoreUrl="/two.xml" onPlayerStateChange={state} onActiveNotesChange={notes} />);
  act(() => { old.playerReady.emit(); old.playerStateChanged.emit({ state: 1 }); old.activeBeatsChanged.emit({ activeBeats: [{ notes: [{ string: 1, fret: 0 }] }] }); });
  expect(state).not.toHaveBeenCalled(); expect(notes).not.toHaveBeenCalled(); expect(old.destroy).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: /play/i })).toBeDisabled(); view.unmount();
  act(() => mock.apis[1].playerStateChanged.emit({ state: 1 })); expect(state).not.toHaveBeenCalled();
});
it("latches SDK failure so late readiness cannot start playback", () => {
  render(<AlphaTabViewer scoreUrl="/bad.xml" />); const api = mock.apis[0];
  act(() => { api.error.emit(new Error("synthetic failure")); api.playerReady.emit(); });
  expect(screen.getByText(/synthetic failure/)).toBeVisible(); expect(screen.getByRole("button", { name: /play/i })).toBeDisabled();
});


it("installs readable layout on the selected local renderer and ignores a stale score load", () => {
  const view = render(<AlphaTabViewer scoreUrl="/one.xml" />);
  const first = mock.apis[0];
  expect(first.settings.core.useWorkers).toBe(false);
  act(() => first.scoreLoaded.emit());
  expect(layout.install).toHaveBeenCalledWith(first);
  view.rerender(<AlphaTabViewer scoreUrl="/two.xml" />);
  act(() => first.scoreLoaded.emit());
  expect(layout.install).toHaveBeenCalledTimes(1);
  act(() => mock.apis[1].scoreLoaded.emit());
  expect(layout.install).toHaveBeenCalledTimes(2);
});

it("latches a layout installation error and refuses late playback readiness", () => {
  layout.install.mockImplementation(() => { throw new Error("unsupported layout"); });
  render(<AlphaTabViewer scoreUrl="/score.xml" />);
  const api = mock.apis[0];
  act(() => { api.scoreLoaded.emit(); api.playerReady.emit(); });
  expect(api.destroy).toHaveBeenCalledOnce();
  expect(screen.getByText(/unsupported layout/)).toBeVisible();
  expect(screen.getByRole("button", { name: /play/i })).toBeDisabled();
});
