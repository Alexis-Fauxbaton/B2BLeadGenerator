import {
  CHANNEL_LABELS,
  LIFECYCLE_LABEL_LABELS,
  LIFECYCLE_LABEL_STYLES,
  POPULATION_LABELS,
  POPULATION_STYLES,
  QUALIF_ISSUE_DOT,
  QUALIF_ISSUE_STYLES,
  SOURCE_LABELS,
  STATUS_LABELS,
  STATUS_STYLES,
  formatIssueChip,
  scoreTier,
} from "@/lib/labels";

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

export function PopulationBadge({ population }: { population: string }) {
  // 'chr' = défaut discret ; 'architecte' = teinte indigo distinctive.
  const cls = POPULATION_STYLES[population] ?? POPULATION_STYLES.chr;
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${cls}`}
      title={population === "architecte" ? "Prescripteur (architecte d'intérieur)" : "Établissement CHR"}
    >
      {POPULATION_LABELS[population] ?? population}
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

// --- Cycle de vie : stage (vie du lieu) / chaleur (moment d'achat) / fraîcheur --

const STAGE_STYLES: Record<string, string> = {
  "pré-ouverture": "bg-violet-50 text-violet-700 ring-violet-200",
  "ouvert récemment": "bg-sky-50 text-sky-700 ring-sky-200",
  "établi": "bg-amber-50 text-amber-700 ring-amber-200",
  "fermé": "bg-slate-100 text-slate-400 ring-slate-200",
};

const HEAT_STYLES: Record<string, { dot: string; cls: string }> = {
  chaud: { dot: "bg-red-500", cls: "bg-red-50 text-red-600 ring-red-200" },
  tiède: { dot: "bg-orange-400", cls: "bg-orange-50 text-orange-600 ring-orange-200" },
  froid: { dot: "bg-slate-300", cls: "bg-slate-100 text-slate-400 ring-slate-200" },
};

const FRESH_STYLES: Record<string, string> = {
  fraîche: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  "à rafraîchir": "bg-amber-50 text-amber-700 ring-amber-200",
  périmée: "bg-slate-100 text-slate-400 ring-slate-200",
};

export function StageBadge({ stage }: { stage: string }) {
  const cls = STAGE_STYLES[stage] ?? "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}>
      {stage}
    </span>
  );
}

export function HeatBadge({ heat }: { heat: string }) {
  const s = HEAT_STYLES[heat] ?? HEAT_STYLES.froid;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} /> {heat}
    </span>
  );
}

export function FreshnessBadge({ freshness }: { freshness: string }) {
  const cls = FRESH_STYLES[freshness] ?? "bg-slate-100 text-slate-400 ring-slate-200";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${cls}`}>
      {freshness}
    </span>
  );
}

// Puce « dernière issue » (§2.2 du design qualification) : dérivée à la volée,
// jamais persistée sur la fiche — affichage seul, pour prioriser à l'œil dans
// les listes (/followups, journal). `raison` prime sur `issue` si connue.
export function IssueBadge({
  issue,
  raison,
}: {
  issue: string;
  raison?: string | null;
}) {
  const cls = QUALIF_ISSUE_STYLES[issue] ?? "bg-slate-100 text-slate-500 ring-slate-200";
  const dot = QUALIF_ISSUE_DOT[issue] ?? "bg-slate-400";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${cls}`}
      title="Dernier contact (affichage seul — n'écrit jamais sur la fiche)"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {formatIssueChip(issue, raison)}
    </span>
  );
}

// Label de cycle de vie PERSISTÉ (funnel Insta, brique 3bis) — distinct du
// StageBadge (dérivé). NULL pour les sources registre (BODACC/Sirene) : rien
// n'est affiché dans ce cas.
export function LifecycleBadge({ label }: { label: string | null | undefined }) {
  if (!label) return null;
  const cls = LIFECYCLE_LABEL_STYLES[label] ?? "bg-slate-100 text-slate-500 ring-slate-200";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}>
      {LIFECYCLE_LABEL_LABELS[label] ?? label}
    </span>
  );
}
