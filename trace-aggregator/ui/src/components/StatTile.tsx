interface Props {
  label: string;
  value: string | number;
  unit?: string;
  tone?: "default" | "warn" | "alert";
}

export function StatTile({ label, value, unit, tone = "default" }: Props) {
  const valueColor =
    tone === "alert"
      ? "text-cherry-light"
      : tone === "warn"
      ? "text-amber"
      : "text-cream-50";

  return (
    <div className="hairline rounded-sm p-5 bg-ink-700/40">
      <div className="eyebrow mb-2">{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span className={`font-display text-[34px] leading-none tracking-tighter ${valueColor}`}>
          {value}
        </span>
        {unit && (
          <span className="font-mono text-[11px] text-cream-500 uppercase">{unit}</span>
        )}
      </div>
    </div>
  );
}
