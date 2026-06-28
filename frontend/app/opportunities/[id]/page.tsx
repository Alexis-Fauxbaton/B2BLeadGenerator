"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  MapPin,
  Sparkles,
  Target,
  Compass,
  FileText,
  ExternalLink,
  History,
  Calendar,
  User,
  Loader2,
  Phone,
  Mail,
  Instagram,
  Globe,
  Star,
} from "lucide-react";
import { api } from "@/lib/api";
import type { GeneratedMessages, OpportunityRead } from "@/lib/types";
import {
  ACTION_LABELS,
  CHANNEL_LABELS,
  STATUS_LABELS,
  STATUS_ORDER,
  formatDate,
} from "@/lib/labels";
import { ChannelBadge, ScoreBadge, SignalBadge, SourceBadge, StatusBadge } from "@/components/Badges";
import { Loading, ErrorState } from "@/components/States";
import CopyButton from "@/components/CopyButton";

const STATUS_ACTIONS = [
  { status: "contacte", label: "Marquer comme contacté" },
  { status: "interesse", label: "Marquer intéressé" },
  { status: "rdv", label: "Marquer RDV" },
  { status: "gagne", label: "Marquer gagné" },
  { status: "perdu", label: "Marquer perdu" },
];

export default function OpportunityDetailPage() {
  const params = useParams();
  const id = Number(params.id);

  const [opp, setOpp] = useState<OpportunityRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [msgSource, setMsgSource] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [busyStatus, setBusyStatus] = useState(false);

  const reload = () =>
    api.getOpportunity(id).then(setOpp).catch((e) => setError(e.message));

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const generate = async () => {
    setGenerating(true);
    try {
      const m: GeneratedMessages = await api.generateMessages(id);
      setMsgSource(m.source);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const changeStatus = async (status: string) => {
    setBusyStatus(true);
    try {
      await api.updateStatus(id, { status });
      await reload();
    } finally {
      setBusyStatus(false);
    }
  };

  const planFollowUp = async () => {
    if (!followUp) return;
    setBusyStatus(true);
    try {
      await api.updateStatus(id, { status: "relance", next_follow_up_date: followUp });
      setFollowUp("");
      await reload();
    } finally {
      setBusyStatus(false);
    }
  };

  if (error) return <ErrorState message={error} />;
  if (!opp) return <Loading label="Chargement de la fiche…" />;

  const hasMessages = Boolean(
    opp.generated_instagram_dm || opp.generated_email || opp.generated_linkedin
  );

  return (
    <div className="p-8">
      <Link
        href="/opportunities"
        className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800"
      >
        <ArrowLeft size={16} /> Retour aux opportunités
      </Link>

      {/* En-tête fiche */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold text-slate-900">{opp.establishment_name}</h1>
              <StatusBadge status={opp.status} />
              <SourceBadge source={opp.source} />
            </div>
            <p className="mt-1 flex items-center gap-2 text-sm text-slate-500">
              <span className="capitalize">{opp.establishment_type}</span> ·
              <MapPin size={14} /> {opp.address}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <SignalBadge label={opp.main_signal} />
              {opp.secondary_signals.map((s) => (
                <span key={s} className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
                  {s}
                </span>
              ))}
            </div>
          </div>
          <div className="text-right">
            <ScoreBadge score={opp.opportunity_score} />
            <p className="mt-2 text-xs text-slate-400">Détecté le {formatDate(opp.detection_date)}</p>
            <p className="text-xs text-slate-400">Timing estimé : <span className="font-medium text-slate-600">{opp.estimated_timing}</span></p>
          </div>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Colonne principale */}
        <div className="space-y-6 lg:col-span-2">
          {/* Signal & preuve */}
          <Section icon={FileText} title="Signal & preuve">
            <p className="text-sm text-slate-600">{opp.proof_text}</p>
            {opp.proof_url && (
              <a
                href={opp.proof_url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-sm text-brand-600 hover:text-brand-700"
              >
                <ExternalLink size={14} /> Source (fictive)
              </a>
            )}
            <div className="mt-4 space-y-2">
              {opp.signals.map((s) => (
                <div key={s.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm">
                  <div>
                    <span className="font-medium text-slate-700">{s.signal_type}</span>
                    <span className="ml-2 text-xs text-slate-400">{s.source}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-slate-400">
                    <span>conf. {(s.confidence_score * 100).toFixed(0)}%</span>
                    <span>{formatDate(s.signal_date)}</span>
                  </div>
                </div>
              ))}
            </div>
          </Section>

          {/* Scoring */}
          <Section icon={Target} title="Score d'opportunité">
            <div className="flex items-center gap-3">
              <ScoreBadge score={opp.opportunity_score} />
            </div>
            <p className="mt-3 text-sm text-slate-600">{opp.score_reason}</p>
          </Section>

          {/* Canal */}
          <Section icon={Compass} title="Canal recommandé">
            <ChannelBadge channel={opp.recommended_channel} />
            <p className="mt-3 text-sm text-slate-600">{opp.channel_reason}</p>
          </Section>

          {/* Messages */}
          <Section icon={Sparkles} title="Messages de contact">
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={generate}
                disabled={generating}
                className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {generating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
                {hasMessages ? "Régénérer les messages" : "Générer messages"}
              </button>
              {msgSource && (
                <span className="text-xs text-slate-400">
                  Source : {msgSource === "openai" ? "OpenAI" : "templates locaux"}
                </span>
              )}
            </div>

            {hasMessages && (
              <div className="mt-4 space-y-4">
                <MessageBlock title="DM Instagram" content={opp.generated_instagram_dm} copyLabel="Copier DM Instagram" />
                <MessageBlock title="Email" content={opp.generated_email} copyLabel="Copier email" />
                <MessageBlock title="LinkedIn" content={opp.generated_linkedin} copyLabel="Copier message LinkedIn" />
                <MessageBlock title="Script d'appel" content={opp.generated_call_script} copyLabel="Copier script d'appel" />
              </div>
            )}
          </Section>

          {/* Historique */}
          <Section icon={History} title="Historique de contact">
            {opp.contact_history.length === 0 ? (
              <p className="text-sm text-slate-400">Aucune interaction enregistrée.</p>
            ) : (
              <ol className="relative space-y-4 border-l border-slate-200 pl-5">
                {opp.contact_history.map((h) => (
                  <li key={h.id} className="relative">
                    <span className="absolute -left-[23px] top-1 h-2.5 w-2.5 rounded-full bg-brand-400 ring-4 ring-white" />
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-700">
                        {ACTION_LABELS[h.action_type] ?? h.action_type}
                      </span>
                      {h.status && <StatusBadge status={h.status} />}
                    </div>
                    {h.note && <p className="text-sm text-slate-500">{h.note}</p>}
                    <p className="text-xs text-slate-400">{formatDate(h.created_at)}</p>
                  </li>
                ))}
              </ol>
            )}
          </Section>
        </div>

        {/* Colonne latérale : infos + actions */}
        <div className="space-y-6">
          <Section icon={Phone} title="Contact">
            <ContactBlock opp={opp} />
          </Section>

          <Section icon={User} title="Qualification">
            <InfoRow label="Décideur probable" value={opp.decision_maker ?? "—"} />
            <InfoRow label="Besoins probables" value={opp.probable_needs.join(", ") || "—"} />
            <InfoRow label="Timing estimé" value={opp.estimated_timing} />
            <InfoRow
              label="Prochaine relance"
              value={formatDate(opp.next_follow_up_date)}
            />
            {opp.siren && (
              <div className="flex justify-between gap-4 border-b border-slate-50 py-2 last:border-0">
                <span className="text-sm text-slate-400">SIREN</span>
                <a
                  href={`https://annuaire-entreprises.data.gouv.fr/entreprise/${opp.siren}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-right text-sm font-medium text-brand-600 hover:text-brand-700"
                >
                  {opp.siren}
                </a>
              </div>
            )}
          </Section>

          <Section icon={Calendar} title="Actions">
            <div className="flex flex-col gap-2">
              {STATUS_ACTIONS.map((a) => (
                <button
                  key={a.status}
                  onClick={() => changeStatus(a.status)}
                  disabled={busyStatus || opp.status === a.status}
                  className="rounded-lg border border-slate-200 px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {a.label}
                </button>
              ))}
            </div>

            <div className="mt-4 border-t border-slate-100 pt-4">
              <label className="text-xs font-medium text-slate-500">Planifier une relance</label>
              <div className="mt-1.5 flex gap-2">
                <input
                  type="date"
                  value={followUp}
                  onChange={(e) => setFollowUp(e.target.value)}
                  className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
                />
                <button
                  onClick={planFollowUp}
                  disabled={!followUp || busyStatus}
                  className="rounded-lg bg-amber-500 px-3 py-2 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                >
                  OK
                </button>
              </div>
            </div>
          </Section>

          <Section icon={Compass} title="Statuts">
            <div className="flex flex-wrap gap-1.5">
              {STATUS_ORDER.map((s) => (
                <button
                  key={s}
                  onClick={() => changeStatus(s)}
                  disabled={busyStatus}
                  className={`rounded-md px-2 py-1 text-xs ${
                    opp.status === s
                      ? "bg-brand-600 text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                  }`}
                >
                  {STATUS_LABELS[s]}
                </button>
              ))}
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Target;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Icon size={16} className="text-brand-500" /> {title}
      </h2>
      {children}
    </div>
  );
}

function reviewFreshness(n: number | null) {
  if (n === null || n === undefined)
    return { label: "Non trouvé sur Google", cls: "text-slate-400", sub: "aucune empreinte en ligne" };
  if (n <= 20)
    return { label: `${n} avis · tout récent`, cls: "text-emerald-600", sub: "fenêtre d'aménagement probablement ouverte" };
  if (n >= 200)
    return { label: `${n} avis · déjà installé`, cls: "text-amber-600", sub: "établissement établi (achat probablement passé)" };
  return { label: `${n} avis`, cls: "text-slate-600", sub: "" };
}

function ConfidenceChip({ level }: { level: string | null }) {
  const map: Record<string, { label: string; cls: string }> = {
    haute: { label: "fiable", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
    moyenne: { label: "à confirmer", cls: "bg-amber-50 text-amber-700 ring-amber-200" },
  };
  const c = map[level ?? ""] ?? { label: "non vérifié", cls: "bg-slate-100 text-slate-400 ring-slate-200" };
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${c.cls}`}>
      {c.label}
    </span>
  );
}

function ContactBlock({ opp }: { opp: OpportunityRead }) {
  const instaUrl = opp.instagram
    ? `https://instagram.com/${opp.instagram.replace(/^@/, "")}`
    : null;
  // Précision d'abord : on n'affiche les contacts établissement que si la
  // confiance est haute ou moyenne ; sinon "à trouver".
  const estabShown = opp.contact_confidence === "haute" || opp.contact_confidence === "moyenne";
  const hasEstabValues = Boolean(opp.phone || opp.email || opp.website || opp.instagram);
  const fresh = reviewFreshness(opp.review_count);
  // Décideur : email affiché seulement si confiance haute.
  const decideurEmailShown = opp.decision_maker_confidence === "haute" && Boolean(opp.decision_maker_email);

  return (
    <div className="space-y-4">
      {/* Bloc établissement */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Établissement</span>
          <ConfidenceChip level={opp.contact_confidence} />
        </div>
        {estabShown && hasEstabValues ? (
          <div className="space-y-2">
            {opp.phone && (
              <ContactRow icon={Phone} href={`tel:${opp.phone.replace(/\s/g, "")}`} text={opp.phone} action="Appeler" />
            )}
            {opp.email && (
              <ContactRow icon={Mail} href={`mailto:${opp.email}`} text={opp.email} action="Écrire" />
            )}
            {instaUrl && (
              <ContactRow icon={Instagram} href={instaUrl} text={`@${opp.instagram!.replace(/^@/, "")}`} action="Ouvrir" external />
            )}
            {opp.website && (
              <ContactRow icon={Globe} href={opp.website} text={opp.website.replace(/^https?:\/\//, "")} action="Visiter" external />
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-400">
            {hasEstabValues ? "Contact trouvé mais non vérifié — à confirmer." : "Contact établissement à trouver."}
          </p>
        )}

        {/* Match Google / fraîcheur */}
        <div className="flex items-start gap-2 pt-1">
          <Star size={15} className="mt-0.5 shrink-0 text-slate-400" />
          <div>
            <p className={`text-sm font-medium ${fresh.cls}`}>{fresh.label}</p>
            {fresh.sub && <p className="text-xs text-slate-400">{fresh.sub}</p>}
          </div>
        </div>
      </div>

      {/* Bloc décideur(s) */}
      <div className="space-y-2 border-t border-slate-100 pt-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Décideur{(opp.dirigeants?.length ?? 0) > 1 ? "s" : ""}
        </span>
        {(opp.dirigeants?.length ?? 0) > 0 ? (
          <div className="space-y-1.5">
            {opp.dirigeants.map((d) => (
              <div key={d} className="flex items-center gap-2 text-sm text-slate-700">
                <User size={14} className="shrink-0 text-slate-400" />
                <span>{d}</span>
              </div>
            ))}
          </div>
        ) : (
          <InfoRow label="Nom" value={opp.decision_maker ?? "—"} />
        )}
        {decideurEmailShown ? (
          <ContactRow icon={Mail} href={`mailto:${opp.decision_maker_email}`} text={opp.decision_maker_email!} action="Écrire" />
        ) : (
          <p className="text-sm text-slate-400">Email du décideur à trouver.</p>
        )}
      </div>
    </div>
  );
}

function ContactRow({
  icon: Icon,
  href,
  text,
  action,
  external,
}: {
  icon: typeof Phone;
  href: string;
  text: string;
  action: string;
  external?: boolean;
}) {
  return (
    <a
      href={href}
      target={external ? "_blank" : undefined}
      rel={external ? "noreferrer" : undefined}
      className="group flex items-center gap-2.5 rounded-lg border border-slate-200 px-3 py-2 hover:border-brand-300 hover:bg-brand-50"
    >
      <Icon size={15} className="shrink-0 text-brand-500" />
      <span className="min-w-0 flex-1 truncate text-sm text-slate-700" title={text}>
        {text}
      </span>
      <span className="text-xs font-medium text-slate-400 group-hover:text-brand-600">{action}</span>
    </a>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-slate-50 py-2 last:border-0">
      <span className="text-sm text-slate-400">{label}</span>
      <span className="text-right text-sm font-medium text-slate-700">{value}</span>
    </div>
  );
}

function MessageBlock({
  title,
  content,
  copyLabel,
}: {
  title: string;
  content: string | null;
  copyLabel: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</span>
        <CopyButton text={content} label={copyLabel} />
      </div>
      <pre className="whitespace-pre-wrap px-3 py-3 text-sm text-slate-700 font-sans">{content}</pre>
    </div>
  );
}
