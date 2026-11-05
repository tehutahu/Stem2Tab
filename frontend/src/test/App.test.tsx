import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
vi.mock("../pages/MidiTrial", () => ({ default: ({ jobSource, tempoSourceJobId }: { jobSource?: { jobId: string; name: string }; tempoSourceJobId?: string }) => <><p data-testid="import-source">{jobSource?.jobId} / {jobSource?.name}</p><p data-testid="tempo-source">{tempoSourceJobId}</p></> }));
import App from "../App";
it("connects normal-job results to the shared MIDI workflow", () => {
  render(<MemoryRouter initialEntries={["/midi-trial/from-job/12345678-1234-4123-8123-123456789012?name=bass.mid"]}><App /></MemoryRouter>);
  expect(screen.getByTestId("import-source")).toHaveTextContent("12345678-1234-4123-8123-123456789012 / bass.mid");
  expect(screen.getByRole("navigation", { name: "作業の入口" })).toBeVisible();
  expect(screen.getByRole("link", { name: "音源から採譜（MuScriptor）" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "MuScriptorで採譜（実験・配置確認）" })).toHaveAttribute("href", "/muscriptor-trial");
});

it("keeps normal navigation visible and experimental routes in an operable disclosure", () => {
  render(<MemoryRouter initialEntries={["/midi-trial"]}><App /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "MIDIから五線譜・TAB・任意補正" })).toHaveAttribute("aria-current", "page");
  const summary = screen.getByText("実験・従来方式");
  const disclosure = summary.closest("details");
  expect(disclosure).not.toHaveAttribute("open");
  fireEvent.click(summary);
  expect(disclosure).toHaveAttribute("open");
  expect(screen.getByRole("link", { name: "Basic Pitch（従来方式・別実行）" })).toHaveAttribute("href", "/basic-pitch");
  expect(screen.getByRole("link", { name: "本文へ移動" })).toHaveAttribute("href", "#main-content");
  expect(screen.getByRole("main")).toHaveAttribute("tabindex", "-1");
});

it("opens the experimental navigation when visiting its route directly", () => {
  render(<MemoryRouter initialEntries={["/basic-pitch"]}><App /></MemoryRouter>);
  expect(screen.getByText("実験・従来方式").closest("details")).toHaveAttribute("open");
  expect(screen.getByRole("link", { name: "Basic Pitch（従来方式・別実行）" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { name: "Basic Pitchで採譜する" })).toBeVisible();
});

it("passes an explicit saved-audio job hint while preserving view/spacing query navigation", () => {
  render(<MemoryRouter initialEntries={["/midi-trial/saved/12345678-1234-4123-8123-123456789012?view=staff&spacing=time&tempo_source_job=23456789-1234-4123-8123-123456789012"]}><App /></MemoryRouter>);
  expect(screen.getByTestId("tempo-source")).toHaveTextContent("23456789-1234-4123-8123-123456789012");
});
