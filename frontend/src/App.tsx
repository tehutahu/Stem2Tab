import { Link, NavLink, Route, Routes, useLocation, useParams, useSearchParams } from "react-router-dom";

import MidiTrial from "./pages/MidiTrial";
import MuscriptorTrial from "./pages/MuscriptorTrial";
import ListeningTrial from "./pages/ListeningTrial";
import NotationTrial from "./pages/NotationTrial";
import Demo from "./pages/Demo";
import Upload from "./pages/Upload";
import Transcribe from "./pages/Transcribe";

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
  const { pathname } = useLocation();
  const experimental = pathname === "/muscriptor-trial" || pathname === "/basic-pitch";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">本文へ移動</a>
      <header className="app-header">
        <Link className="brand" to="/" aria-label="Stem2Tab ホーム">
          <span className="brand-mark" aria-hidden="true">s<span>2</span>t</span>
          <div><h1>Stem2Tab</h1><small>BASS TRANSCRIPTION WORKSPACE</small></div>
        </Link>
        <span className="app-header-note">聴いて、確かめて、弾く。</span>
      </header>
      <nav className="app-nav" aria-label="作業の入口">
        <div className="primary-nav">
          <NavLink to="/" end>音源から採譜（MuScriptor）</NavLink>
          <NavLink to="/midi-trial" className={() => pathname.startsWith("/midi-trial") ? "active" : ""}>MIDIから五線譜・TAB・任意補正</NavLink>
        </div>
        <details className="advanced-nav" open={experimental}>
          <summary>実験・従来方式</summary>
          <div className="advanced-nav-links">
            <NavLink to="/muscriptor-trial">MuScriptorで採譜（実験・配置確認）</NavLink>
            <NavLink to="/basic-pitch">Basic Pitch（従来方式・別実行）</NavLink>
          </div>
        </details>
      </nav>
      <main className="app-content" id="main-content" tabIndex={-1}>
        <Routes>
        <Route path="/notation-trial/:trialId" element={<SavedNotationTrial />} />
        <Route path="/listening-trial/:trialId" element={<SavedListeningTrial />} />
        <Route path="/muscriptor-trial" element={<MuscriptorTrial />} />
        <Route path="/midi-trial/from-job/:jobId" element={<JobMidiImport />} />
        <Route path="/midi-trial" element={<MidiTrial />} />
        <Route path="/midi-trial/saved/:trialId" element={<SavedMidiTrial />} />
        <Route path="/" element={<Transcribe />} />
        <Route path="/basic-pitch" element={<Upload />} />
        <Route path="/demo/:jobId" element={<Demo />} />
        </Routes>
      </main>
      <footer className="app-footer"><span>Stem2Tab</span><p>元データを保持。運指・量子化・GP5の別版は、必要な場合だけ選べます。</p></footer>
    </div>
  );
}

export default App;
