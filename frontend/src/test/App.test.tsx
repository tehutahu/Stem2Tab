import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
vi.mock("../pages/MidiTrial", () => ({ default: ({ jobSource }: { jobSource?: { jobId: string; name: string } }) => <p data-testid="import-source">{jobSource?.jobId} / {jobSource?.name}</p> }));
import App from "../App";
it("connects normal-job results to the shared MIDI workflow", () => {
  render(<MemoryRouter initialEntries={["/midi-trial/from-job/12345678-1234-4123-8123-123456789012?name=bass.mid"]}><App /></MemoryRouter>);
  expect(screen.getByTestId("import-source")).toHaveTextContent("12345678-1234-4123-8123-123456789012 / bass.mid");
  expect(screen.getByRole("navigation", { name: "作業の入口" })).toBeVisible();
  expect(screen.getByRole("link", { name: /MuScriptor/ })).toHaveAttribute("href", "/muscriptor-trial");
});
