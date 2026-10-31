import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import MidiTrial from "../pages/MidiTrial";
vi.mock("../components/MidiTrialViewer", () => ({ default: ({bassTab,scoreUrl,variant,spacing}: {bassTab?: {tuning:number[]};scoreUrl:string;variant?:string;spacing?:string}) => <div data-testid="viewer" data-url={scoreUrl} data-variant={variant} data-spacing={spacing}>XML viewer{bassTab && <span>TAB {bassTab.tuning.join(",")}</span>}</div> }));
const summary = { trial_id: "fresh-id", source_kind: "local-midi", status: "gp5_blocked_out_of_range", original_filename: "bass.mid", input_bytes: 20, input_note_count: 1, source_duration_seconds: 1, last_note_end_seconds: 1, input_sha256: "a".repeat(64), storage_clock: { ppq: 960, tempo_bpm: 120, meter: "4/4", musical_tempo_and_meter_verified: false }, config: {}, gp5: { requested: true, status: "unmappable", mapped_count: 0, unmappable: [{ source_index: 0, midi: 20, start: 0, end: 1, reason: "below tuning" }] }, warnings: [], artifacts: [{ key: "original-midi", kind: "midi", filename: "bass.mid", bytes: 20, sha256: "a".repeat(64), url: "/api/v1/midi-trials/fresh-id/files/original-midi" }, { key: "storage-clock-xml", kind: "xml", filename: "clock.xml", bytes: 20, sha256: "a".repeat(64), url: "/api/v1/midi-trials/fresh-id/files/storage-clock-xml" }], error: null };
function selectInput(): void {
  fireEvent.change(screen.getByLabelText("保存MIDI"), { target: { files: [new File(["midi"], "bass.mid")] } });
  fireEvent.change(screen.getByLabelText("入力の由来"), { target: { value: "local-midi" } });
  fireEvent.change(screen.getByLabelText("弦構成"), { target: { value: "bass4" } });
}
afterEach(() => {vi.unstubAllGlobals(); window.history.replaceState({},"","/");});
const savedId = "0e8ac6da-bceb-4675-aa1a-9518eb3afb89";
const savedSummary = { ...summary, trial_id: savedId, artifacts: summary.artifacts.map((a) => ({ ...a, url: a.url.replace("fresh-id", savedId) })) };
it("offers time-proportional spacing explicitly and carries it across notation-clock links",async()=>{
  window.history.replaceState({},"","/?view=tab&notation=double&spacing=time");
  const fetchMock=vi.fn().mockResolvedValue({ok:true,json:async()=>({...savedSummary,storage_clock:{...summary.storage_clock,tempo_bpm:60}})});vi.stubGlobal("fetch",fetchMock);
  render(<MidiTrial savedTrialId={savedId}/>);
  expect(await screen.findByTestId("viewer")).toHaveAttribute("data-spacing","time");
  expect(screen.getByRole("link",{name:"原版 60 BPMで表示"})).toHaveAttribute("href",`/midi-trial/saved/${savedId}?view=tab&spacing=time`);
  fireEvent.change(screen.getByLabelText("横配置"),{target:{value:"engraved"}});
  expect(screen.getByTestId("viewer")).toHaveAttribute("data-spacing","engraved");
  expect(screen.getByRole("link",{name:"原版 60 BPMで表示"})).toHaveAttribute("href",`/midi-trial/saved/${savedId}?view=tab`);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("opens the explicit double notation clock while retaining original links and assumptions",async()=>{
  window.history.replaceState({},"","/?view=tab&notation=double");
  const body={...savedSummary,storage_clock:{...summary.storage_clock,tempo_bpm:60},config:{strings:4,tuning_midi:[28,33,38,43],max_fret:24}};
  const fetchMock=vi.fn().mockResolvedValue({ok:true,json:async()=>body});vi.stubGlobal("fetch",fetchMock);
  render(<MidiTrial savedTrialId={savedId}/>);
  const viewer=await screen.findByTestId("viewer");
  expect(viewer).toHaveAttribute("data-url",`/api/v1/midi-trials/${savedId}/notation-clock/double.musicxml`);
  expect(viewer).toHaveAttribute("data-variant","notation-clock");
  expect(screen.getByText(/表示中: 候補 120 BPM/)).toBeVisible();
  expect(screen.getByText(/4→2秒、小節数は2倍/)).toBeVisible();
  expect(screen.getByText(/拍原点0秒/)).toHaveTextContent("未確認");
  expect(screen.getByText(/この原時刻の記譜テンポ変換ではGP5/)).toBeVisible();
  expect(screen.getByRole("link",{name:"原版 60 BPMで表示"})).toHaveAttribute("href",`/midi-trial/saved/${savedId}?view=tab`);
  expect(screen.getByRole("link",{name:/元MIDI（/})).toHaveAttribute("href",body.artifacts[0].url);
  expect(screen.getByRole("link",{name:/時刻保持MusicXML（保存/})).toHaveAttribute("href",body.artifacts[1].url);
  fireEvent.click(screen.getByRole("checkbox",{name:/五線譜/}));
  expect(screen.getByRole("link",{name:"原版 60 BPMで表示"})).toHaveAttribute("href",`/midi-trial/saved/${savedId}?view=staff`);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("opens a saved result by GET without selecting, uploading, or converting a file", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => savedSummary }); vi.stubGlobal("fetch", fetchMock);
  render(<MidiTrial savedTrialId={savedId} />);
  expect(await screen.findByText("XML viewer")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/midi-trials/${savedId}`);
  expect(fetchMock.mock.calls[0][1].method).toBeUndefined(); expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
  expect(document.querySelector('input[type="file"]')).toBeNull();
});
it("selects a read-only original-time TAB view using the saved tuning without another request", async()=>{
  window.history.replaceState({},"","/?view=tab");
  const fetchMock=vi.fn().mockResolvedValue({ok:true,json:async()=>({...savedSummary,config:{strings:4,tuning_midi:[28,33,38,43],max_fret:24}})});
  vi.stubGlobal("fetch",fetchMock);render(<MidiTrial savedTrialId={savedId}/>);
  expect(await screen.findByText("TAB 28,33,38,43")).toBeVisible();
  const toggle=screen.getByRole("checkbox",{name:/五線譜/});expect(toggle).toBeChecked();
  fireEvent.click(toggle);expect(screen.queryByText("TAB 28,33,38,43")).toBeNull();
  fireEvent.click(toggle);expect(screen.getByText("TAB 28,33,38,43")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledTimes(1);expect(screen.getByRole("link",{name:/元MIDI/})).toHaveAttribute("href",savedSummary.artifacts[0].url);
});
it("does not invent missing tuning for an explicitly requested TAB view",async()=>{
  window.history.replaceState({},"","/?view=tab");
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>savedSummary}));
  render(<MidiTrial savedTrialId={savedId}/>);
  expect(await screen.findByRole("alert")).toHaveTextContent("保存済み弦設定");
  expect(screen.getByRole("checkbox",{name:/五線譜/})).toBeDisabled();
  expect(screen.getByRole("link",{name:/元MIDI/})).toBeVisible();
});
it("rejects an invalid saved ID before fetching", () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  render(<MidiTrial savedTrialId="../../outside" />);
  expect(screen.getByRole("alert")).toHaveTextContent("試行IDが不正"); expect(fetchMock).not.toHaveBeenCalled();
});
it.each([
  { ok: false, status: 404, body: savedSummary },
  { ok: true, status: 200, body: summary },
  { ok: true, status: 200, body: { ...savedSummary, artifacts: [null] } },
])("rejects missing, mismatched, or malformed saved results", async ({ ok, status, body }) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok, status, json: async () => body }));
  render(<MidiTrial savedTrialId={savedId} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("読み込めません"); expect(screen.queryByText("XML viewer")).not.toBeInTheDocument();
});
it("aborts stale saved-result reads when the ID changes and when unmounted", async () => {
  let resolveOld!: (v: unknown) => void;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise((r) => { resolveOld = r; })).mockImplementationOnce(() => new Promise(() => {}));
  vi.stubGlobal("fetch", fetchMock); const view = render(<MidiTrial savedTrialId={savedId} />);
  view.rerender(<MidiTrial savedTrialId="78358988-0548-4c34-a990-40037aeed244" />);
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  resolveOld({ ok: true, json: async () => savedSummary });
  await waitFor(() => expect(screen.queryByText("XML viewer")).not.toBeInTheDocument());
  view.unmount(); expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true);
});
it.each([{ artifacts: [null] }, { warnings: [{}] }, { artifacts: [{ ...summary.artifacts[0], url: "https://example.invalid/file" }] }])("rejects malformed valid JSON safely: %j", async (invalid) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => ({ ...summary, ...invalid }) }));
  render(<MidiTrial />); selectInput(); fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("応答形式が不正");
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
describe("saved MIDI trial", () => {
  it("requires explicit selections and defaults GP5 off", () => {
    render(<MidiTrial />); expect(screen.getByLabelText("弦構成")).toHaveValue(""); expect(screen.getByRole("checkbox")).not.toBeChecked();
    fireEvent.submit(screen.getByRole("button", { name: "この設定でインポート" }).closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("ファイル");
  });
  it("retains MIDI/XML and reports GP5 blocked notes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => summary }); vi.stubGlobal("fetch", fetchMock);
    render(<MidiTrial />); selectInput(); fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
    expect(await screen.findByText("below tuning")).toBeVisible(); expect(screen.getByRole("link", { name: /元MIDI/ })).toHaveAttribute("href", "/api/v1/midi-trials/fresh-id/files/original-midi");
    expect(await screen.findByText("XML viewer")).toBeVisible();
    const body = fetchMock.mock.calls[0][1].body as FormData; expect(body.get("profile")).toBe("bass4"); expect(body.get("gp5_preview")).toBe("true");
    fireEvent.change(screen.getByLabelText("最大フレット"), { target: { value: "20" } }); expect(screen.queryByText("XML viewer")).not.toBeInTheDocument();
  });
  it("retains available artifacts with a failed HTTP 500 summary", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ ...summary, status: "failed", error: { code: "export_failed", message: "failed export" } }) }));
    render(<MidiTrial />); selectInput(); fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("export_failed"); expect(screen.getByRole("link", { name: /元MIDI/ })).toBeVisible();
  });
  it("aborts and ignores stale responses after settings change", async () => {
    let resolve!: (value: unknown) => void; const fetchMock = vi.fn().mockImplementation(() => new Promise((r) => { resolve = r; })); vi.stubGlobal("fetch", fetchMock);
    render(<MidiTrial />); selectInput(); fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
    fireEvent.change(screen.getByLabelText("弦構成"), { target: { value: "bass5" } }); expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    resolve({ ok: true, json: async () => summary }); await waitFor(() => expect(screen.queryByText(/試行ID:/)).not.toBeInTheDocument()); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("shows malformed JSON and rejects invalid custom tuning", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ json: async () => { throw new Error("bad JSON"); } })); render(<MidiTrial />); selectInput();
    fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" })); expect(await screen.findByRole("alert")).toHaveTextContent("bad JSON");
    fireEvent.change(screen.getByLabelText("弦構成"), { target: { value: "custom" } }); fireEvent.change(screen.getByLabelText(/開放弦MIDI/), { target: { value: "28,28" } });
    fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" })); expect(screen.getByRole("alert")).toHaveTextContent("カスタム");
  });
});


it("ignores an old failure after a new explicit submission and aborts on unmount", async () => {
  let rejectOld!: (reason: Error) => void;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject; })).mockResolvedValueOnce({ ok: true, json: async () => ({ ...summary, trial_id: "second-id", artifacts: summary.artifacts.map((a) => ({ ...a, url: a.url.replace("fresh-id", "second-id") })) }) });
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<MidiTrial />); selectInput(); fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
  fireEvent.change(screen.getByLabelText("最大フレット"), { target: { value: "23" } }); fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
  expect(await screen.findByText("試行ID: second-id")).toBeVisible(); rejectOld(new Error("old failure"));
  await waitFor(() => expect(screen.queryByText(/old failure/)).not.toBeInTheDocument()); expect(fetchMock).toHaveBeenCalledTimes(2);
  fireEvent.change(screen.getByLabelText("最大フレット"), { target: { value: "22" } });
  fetchMock.mockImplementationOnce(() => new Promise(() => {})); fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
  const signal = fetchMock.mock.calls[2][1].signal; view.unmount(); expect(signal.aborted).toBe(true);
});
it("validates file size and frets before sending", () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); render(<MidiTrial />); selectInput();
  fireEvent.change(screen.getByLabelText("最大フレット"), { target: { value: "37" } });
  fireEvent.submit(screen.getByRole("button", { name: "この設定でインポート" }).closest("form")!); expect(screen.getByRole("alert")).toHaveTextContent("0〜36");
  fireEvent.change(screen.getByLabelText("保存MIDI"), { target: { files: [new File([new Uint8Array(2 * 1024 * 1024 + 1)], "large.mid")] } });
  fireEvent.submit(screen.getByRole("button", { name: "この設定でインポート" }).closest("form")!); expect(screen.getByRole("alert")).toHaveTextContent("2 MiB"); expect(fetchMock).not.toHaveBeenCalled();
});

it("imports a completed normal-job MIDI only after explicit instrument selection", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: savedId, status: "SUCCESS", files: ["bass.mid"] }) })
    .mockResolvedValueOnce({ ok: true, blob: async () => new Blob(["synthetic midi"]) })
    .mockResolvedValueOnce({ ok: true, json: async () => summary });
  vi.stubGlobal("fetch", fetcher);
  render(<MidiTrial jobSource={{ jobId: savedId, name: "bass.mid" }} />);
  await screen.findByText(/生成MIDI: bass.mid/);
  expect(fetcher).toHaveBeenCalledTimes(2); // GET only; no inference or import yet
  fireEvent.change(screen.getByLabelText("弦構成"), { target: { value: "bass4" } });
  fireEvent.click(screen.getByRole("button", { name: "この設定でインポート" }));
  await screen.findByText(/試行ID:/);
  const form = fetcher.mock.calls[2][1].body as FormData;
  expect((form.get("file") as File).name).toBe("bass.mid"); expect(form.get("source_kind")).toBe("local-midi"); expect(form.get("gp5_preview")).toBe("false");
});
it("rejects unfinished normal-job output without fetching or importing the MIDI", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: savedId, status: "STARTED", files: ["bass.mid"] }) }); vi.stubGlobal("fetch", fetcher);
  render(<MidiTrial jobSource={{ jobId: savedId, name: "bass.mid" }} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("完了したジョブ"); expect(fetcher).toHaveBeenCalledOnce();
});
it("discards an old job MIDI download after the source changes", async () => {
  let complete!: (value: Blob) => void;
  const second = "78358988-0548-4c34-a990-40037aeed244";
  const fetcher = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: savedId, status: "SUCCESS", files: ["old.mid"] }) })
    .mockResolvedValueOnce({ ok: true, blob: () => new Promise(resolve => { complete = resolve; }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: second, status: "SUCCESS", files: ["new.mid"] }) })
    .mockResolvedValueOnce({ ok: true, blob: async () => new Blob(["new"]) });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<MidiTrial jobSource={{ jobId: savedId, name: "old.mid" }} />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  view.rerender(<MidiTrial jobSource={{ jobId: second, name: "new.mid" }} />);
  await screen.findByText(/生成MIDI: new.mid/);
  expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
  complete(new Blob(["old"]));
  await waitFor(() => expect(screen.getByText(/生成MIDI: new.mid/)).toBeVisible());
  expect(screen.queryByText(/生成MIDI: old.mid/)).toBeNull();
});
