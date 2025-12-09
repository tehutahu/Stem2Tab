import { Route, Routes } from "react-router-dom";

import Demo from "./pages/Demo";
import Upload from "./pages/Upload";

function App(): JSX.Element {
  return (
    <main style={{ margin: "0 auto", maxWidth: 960, padding: "1.5rem" }}>
      <header style={{ marginBottom: "1rem" }}>
        <h1>Stem2Tab</h1>
        <p style={{ color: "#4b5563" }}>
          Upload audio, track progress, and preview tablature with demo playback.
        </p>
      </header>
      <Routes>
        <Route path="/" element={<Upload />} />
        <Route path="/demo/:jobId" element={<Demo />} />
      </Routes>
    </main>
  );
}

export default App;

