import { Route, Routes } from "react-router-dom";
import { Header } from "./components/Header";
import { TraceListPage } from "./pages/TraceListPage";
import { TraceDetailPage } from "./pages/TraceDetailPage";
import { BlamePage } from "./pages/BlamePage";
import { SLOPage } from "./pages/SLOPage";

export default function App() {
  return (
    <div className="min-h-full">
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<TraceListPage />} />
          <Route path="/traces/:id" element={<TraceDetailPage />} />
          <Route path="/blame" element={<BlamePage />} />
          <Route path="/slo" element={<SLOPage />} />
        </Routes>
      </main>
      <footer className="hairline-t mt-20">
        <div className="max-w-[1400px] mx-auto px-8 py-6 flex justify-between items-center">
          <div className="font-mono text-[10px] text-cream-500 uppercase tracking-[0.2em]">
            distributed trace aggregator
          </div>
          <div className="font-display italic text-[13px] text-cream-500">
            built for the multi-agent era
          </div>
        </div>
      </footer>
    </div>
  );
}
