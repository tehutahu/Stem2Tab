import { Link, Route, Routes, useParams, useSearchParams } from "react-router-dom";

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

function JobMidiImport(): JSX.Element {
  const { jobId } = useParams();
  const [query] = useSearchParams();
  const name = query.get("name") ?? "";
  return <MidiTrial key={`${jobId}:${name}`} jobSource={{ jobId: jobId ?? "", name }} />;
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
      <nav aria-label="作業の入口" style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <Link to="/">音源から採譜（Basic Pitch）</Link>
        <Link to="/midi-trial">MIDIから五線譜・TAB・任意補正</Link>
        <Link to="/muscriptor-trial">MuScriptorで採譜（実験・配置確認）</Link>
      </nav>
      <p>生成MIDI → 原時刻の五線譜・TAB → 必要な場合だけ運指・量子化・GP5の別版を選べます。</p>
      <Routes>
        <Route path="/notation-trial/:trialId" element={<SavedNotationTrial />} />
        <Route path="/listening-trial/:trialId" element={<SavedListeningTrial />} />
        <Route path="/muscriptor-trial" element={<MuscriptorTrial />} />
        <Route path="/midi-trial/from-job/:jobId" element={<JobMidiImport />} />
        <Route path="/midi-trial" element={<MidiTrial />} />
        <Route path="/midi-trial/saved/:trialId" element={<SavedMidiTrial />} />
        <Route path="/" element={<Upload />} />
        <Route path="/demo/:jobId" element={<Demo />} />
      </Routes>
    </main>
  );
}

export default App;
