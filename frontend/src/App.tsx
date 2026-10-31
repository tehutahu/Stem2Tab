import { Link, Route, Routes, useParams } from "react-router-dom";

import MidiTrial from "./pages/MidiTrial";
import MuscriptorTrial from "./pages/MuscriptorTrial";
import ListeningTrial from "./pages/ListeningTrial";
import NotationTrial from "./pages/NotationTrial";
import Demo from "./pages/Demo";
import Upload from "./pages/Upload";

function SavedMidiTrial(): JSX.Element {
  const { trialId } = useParams();
  return <MidiTrial key={trialId} savedTrialId={trialId ?? ""} />;
}

function SavedListeningTrial(): JSX.Element {
  const { trialId } = useParams();
  return <ListeningTrial key={trialId} trialId={trialId ?? ""} />;
}

function SavedNotationTrial(): JSX.Element {
  const { trialId } = useParams();
  return <NotationTrial key={trialId} midiTrialId={trialId ?? ""} />;
}

function App(): JSX.Element {
  return (
    <main style={{ margin: "0 auto", maxWidth: 960, padding: "1.5rem" }}>
      <header style={{ marginBottom: "1rem" }}>
        <h1>Stem2Tab</h1>
        <p style={{ color: "#4b5563" }}>
          Upload audio, track progress, and preview tablature with demo playback.
        </p>
      </header>
      <p><Link to="/midi-trial">保存MIDIインポート（実験）</Link></p>
      <p><Link to="/muscriptor-trial">MuScriptor音源実験（ローカル・明示選択）</Link></p>
      <Routes>
        <Route path="/notation-trial/:trialId" element={<SavedNotationTrial />} />
        <Route path="/listening-trial/:trialId" element={<SavedListeningTrial />} />
        <Route path="/muscriptor-trial" element={<MuscriptorTrial />} />
        <Route path="/midi-trial" element={<MidiTrial />} />
        <Route path="/midi-trial/saved/:trialId" element={<SavedMidiTrial />} />
        <Route path="/" element={<Upload />} />
        <Route path="/demo/:jobId" element={<Demo />} />
      </Routes>
    </main>
  );
}

export default App;
