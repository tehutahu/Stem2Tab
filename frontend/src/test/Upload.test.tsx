import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Upload from "../pages/Upload";

describe("Upload page", () => {
  it("disables submit when no file is selected", () => {
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>,
    );

    const button = screen.getByRole("button", { name: /create job/i });
    expect(button).toBeDisabled();
  });

  it("shows status Idle initially", () => {
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>,
    );

    expect(screen.getByText(/Status:/i)).toHaveTextContent("Idle");
  });

  it("enables submit after selecting a file", async () => {
    render(
      <MemoryRouter>
        <Upload />
      </MemoryRouter>,
    );
    const fileInput = screen.getByLabelText(/upload audio/i);

    const file = new File(["dummy"], "test.wav", { type: "audio/wav" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    const button = screen.getByRole("button", { name: /create job/i });
    expect(button).toBeEnabled();
  });
});

