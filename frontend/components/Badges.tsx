import { CHANNEL_LABELS, SOURCE_LABELS, STATUS_LABELS, STATUS_STYLES, scoreTier } from "@/lib/labels";

export function SourceBadge({ source }: { source: string }) {
  const isReal = source !== "demo";
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${
        isReal
          ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
          : "bg-slate-100 text-slate-400 ring-slate-200"
      }`}
      title={isReal ? "Lead réel importé" : "Donnée de démonstration"}
    >
      {SOURCE_LABELS[source] ?? source}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const styles = STATUS_STYLES[status] ?? "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${styles}`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

export function ScoreBadge({ score }: { score: number }) {
  const tier = scoreTier(score);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${tier.classes}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${tier.dot}`} />
      {score}/10 · {tier.label}
    </span>
  );
}

export function ChannelBadge({ channel }: { channel: string }) {
  return (
    <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
      {CHANNEL_LABELS[channel] ?? channel}
    </span>
  );
}

export function SignalBadge({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-md bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-100">
      {label}
    </span>
  );
}
