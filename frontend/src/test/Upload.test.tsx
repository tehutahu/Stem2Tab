import { fireEvent, render, screen } from "@testing-library/react";

import Upload from "../pages/Upload";

describe("Upload page", () => {
  it("disables submit when no file is selected", () => {
    render(<Upload />);

    const button = screen.getByRole("button", { name: /create job/i });
    expect(button).toBeDisabled();
  });

  it("shows status Idle initially", () => {
    render(<Upload />);

    expect(screen.getByText(/Status:/i)).toHaveTextContent("Idle");
  });

  it("enables submit after selecting a file", async () => {
    render(<Upload />);
    const fileInput = screen.getByLabelText(/upload audio/i);

    const file = new File(["dummy"], "test.wav", { type: "audio/wav" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    const button = screen.getByRole("button", { name: /create job/i });
    expect(button).toBeEnabled();
  });
});

