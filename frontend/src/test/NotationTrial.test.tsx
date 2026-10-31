import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import NotationTrial, { validResult } from "../pages/NotationTrial";
const id="11111111-1111-4111-8111-111111111111";
const source={trial_id:id,input_sha256:"a".repeat(64),input_note_count:1,original_filename:"synthetic.mid",status:"completed"};
beforeEach(()=>{ localStorage.clear(); vi.restoreAllMocks(); });
it("requires explicit musical clock and performs no request when incomplete",async()=>{
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>source}); vi.stubGlobal("fetch",fetcher);
  render(<NotationTrial midiTrialId={id}/>); await screen.findByText(/synthetic.mid/);
  fireEvent.change(screen.getByLabelText("弦設定"),{target:{value:"bass4"}});
  fireEvent.click(screen.getByLabelText("明示した音楽クロックで量子化した別版"));
  expect(screen.getByLabelText("四分音符BPM")).toHaveValue(null);
  fireEvent.click(screen.getByText("選択した別版を作成"));
  expect(await screen.findByRole("alert")).toHaveTextContent("すべて必要");
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("locks uncertain submission, recovers by GET, ignores repeat clicks",async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>source}).mockRejectedValueOnce(new Error("offline"));vi.stubGlobal("fetch",fetcher);
  render(<NotationTrial midiTrialId={id}/>);await screen.findByText(/synthetic.mid/);
  fireEvent.change(screen.getByLabelText("弦設定"),{target:{value:"bass4"}});
  fireEvent.click(screen.getByLabelText("前後の移動と同時音を考慮する運指"));
  fireEvent.click(screen.getByText("選択した別版を作成"));fireEvent.click(screen.getByText("選択した別版を作成"));
  await screen.findByText(/offline/);expect(fetcher).toHaveBeenCalledTimes(2);
  const pending=localStorage.getItem(`stem2tab-notation-v1:${id}`)!;
  fetcher.mockResolvedValueOnce({ok:true,json:async()=>({trial_id:pending,midi_trial_id:id,status:"failed",artifacts:[]})});
  fireEvent.click(screen.getByText("既存結果を確認（再実行なし）"));
  await waitFor(()=>expect(screen.getByRole("status")).toHaveTextContent("failed"));
  expect(fetcher.mock.calls[2][0]).toContain(pending);expect(fetcher.mock.calls[2][1]).toBeUndefined();
});
it("rejects remote artifact URLs and source identity mismatch",()=>{
  expect(validResult({trial_id:id,midi_trial_id:id,status:"completed",artifacts:[{key:"original-midi",url:"https://example.invalid/file",sha256:"a".repeat(64)}]},id,id)).toBe(false);
  expect(validResult({trial_id:id,midi_trial_id:"wrong",status:"completed",artifacts:[]},id,id)).toBe(false);
});
it("allows settings correction after a definite pre-execution validation refusal",async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>source}).mockResolvedValueOnce({ok:false,status:422,json:async()=>({detail:[]})});vi.stubGlobal("fetch",fetcher);
  render(<NotationTrial midiTrialId={id}/>);await screen.findByText(/synthetic.mid/);
  fireEvent.change(screen.getByLabelText("弦設定"),{target:{value:"bass4"}});
  fireEvent.click(screen.getByLabelText("前後の移動と同時音を考慮する運指"));fireEvent.click(screen.getByText("選択した別版を作成"));
  expect(await screen.findByRole("alert")).toHaveTextContent("処理は未実行");
  expect(localStorage.getItem(`stem2tab-notation-v1:${id}`)).toBeNull();
  expect(screen.getByText("選択した別版を作成")).toBeEnabled();
});
it("ignores delayed source results after input changes",async()=>{
  let complete:(value:unknown)=>void=()=>{};
  const second="22222222-2222-4222-8222-222222222222";
  const fetcher=vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{complete=resolve;})).mockResolvedValueOnce({ok:true,json:async()=>({...source,trial_id:second,original_filename:"new.mid"})});vi.stubGlobal("fetch",fetcher);
  const view=render(<NotationTrial midiTrialId={id}/>);
  view.rerender(<NotationTrial midiTrialId={second}/>);await screen.findByText(/new.mid/);
  complete({ok:true,json:async()=>source});
  await waitFor(()=>expect(screen.queryByText(/synthetic.mid/)).not.toBeInTheDocument());
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});
