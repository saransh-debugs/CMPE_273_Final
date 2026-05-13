import { NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Traces", end: true },
  { to: "/blame", label: "Blame" },
  { to: "/incidents", label: "Incidents" },
  { to: "/slo", label: "SLO" },
];

export function Header() {
  return (
    <header className="hairline-b sticky top-0 z-30 bg-ink-900/80 backdrop-blur-xl">
      <div className="max-w-[1400px] mx-auto px-8 py-5 flex items-center justify-between">
        {/* Wordmark — serif italic, the design's signature */}
        <NavLink to="/" className="group flex items-baseline gap-3">
          <div className="w-1.5 h-1.5 rounded-full bg-cherry transition-all group-hover:scale-150" />
          <span className="font-display italic text-[22px] leading-none tracking-tighter text-white">
            Trace Aggregator
          </span>
          <span className="hidden md:inline font-mono text-[10px] uppercase tracking-[0.2em] text-500 ml-2">
            v0.1 — multi-agent observability
          </span>
        </NavLink>

        <nav className="flex items-center gap-1">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                `px-3 py-1.5 text-[13px] font-mono uppercase tracking-wider rounded-sm transition-colors ` +
                (isActive
                  ? "text-white bg-ink-600"
                  : "text-fg-300 hover:text-white hover:bg-ink-700")
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}
