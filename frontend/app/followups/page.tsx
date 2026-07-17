"use client";

// Vue « Mes appels » = la journée du closer : relances dues (en retard /
// aujourd'hui / cette semaine) d'abord, puis les fiches jamais appelées avec
// un téléphone valide (à défaut de relance due, on garde le closer en
// mouvement — remontées EN TÊTE si rien n'est dû, cf. revue produit). Lignes
// cliquables vers la fiche + qualification en 2 clics directement depuis la
// liste (sans ouvrir la fiche). Sobre : pas de filtre, pas de tableau, juste
// des listes triées par urgence.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Phone, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { FollowUpBuckets, LastIssue, OpportunityList, QualifTaxonomy } from "@/lib/types";
import { formatDueLabel, POPULATION_LABELS } from "@/lib/labels";
import PageHeader from "@/components/PageHeader";
import { Loading, ErrorState, EmptyState } from "@/components/States";
import { IssueBadge } from "@/components/Badges";
import { QuickQualifyPopover } from "@/components/ContactPanel";

const SECTIONS: { key: keyof FollowUpBuckets; label: string; accent: string }[] = [
  { key: "en_retard", label: "En retard", accent: "text-rose-600" },
  { key: "aujourdhui", label: "Aujourd'hui", accent: "text-amber-600" },
  { key: "cette_semaine", label: "Cette semaine", accent: "text-slate-700" },
];

// Fiches « jamais appelées » : AUCUNE contact_activity (`has_activity=false`,
// backend) + téléphone présent. Volume limité : c'est un complément aux
// relances dues, pas une liste exhaustive.
const NEVER_CALLED_LIMIT = 15;

// Périmètre closer aujourd'hui (pivot Ambient Home) : les deux requêtes de
// cette page (relances dues ET jamais appelés) ciblent la même population,
// pour rester cohérentes entre elles. Même défaut que GET /api/followups
// côté backend — rendu EXPLICITE ici (au lieu de compter sur le défaut
// silencieux) et signalé dans le sous-titre pour ne pas surprendre un closer
// qui travaillerait aussi du CHR. À revoir si les closers traitent un jour
// les deux populations.
const CLOSER_POPULATION = "architecte";

export default function FollowUpsPage() {
  const { user } = useAuth();
  const [assigned, setAssigned] = useState<string>("");
  const [data, setData] = useState<FollowUpBuckets | null>(null);
  const [neverCalled, setNeverCalled] = useState<OpportunityList[] | null>(null);
  const [taxonomy, setTaxonomy] = useState<QualifTaxonomy | null>(null);
  const [lastIssues, setLastIssues] = useState<Record<number, LastIssue>>({});
  const [error, setError] = useState<string | null>(null);

  // Défaut « Mes appels » (pas « toute l'équipe ») dès qu'un closer est loggé
  // (revue produit : un débutant ne doit pas voir/travailler les fiches des
  // collègues par défaut). Appliqué UNE SEULE fois à la connexion de `user` —
  // ne revient pas écraser un toggle manuel ensuite.
  const defaultAppliedRef = useRef(false);
  useEffect(() => {
    if (!defaultAppliedRef.current && user) {
      setAssigned("me");
      defaultAppliedRef.current = true;
    }
  }, [user]);

  useEffect(() => {
    setData(null);
    setNeverCalled(null);
    Promise.all([
      api.getFollowUps(CLOSER_POPULATION, assigned || undefined),
      api
        .getOpportunities({
          has_activity: false,
          population: CLOSER_POPULATION,
          assigned: assigned || undefined,
          sort_by: "score",
          limit: 50,
        })
        .then((page) =>
          page.data
            // Défensif : has_activity=false couvre déjà gagné/perdu dans la
            // quasi-totalité des cas (tout changement de statut journalise une
            // activité) — sauf une fiche seedée directement dans un statut
            // terminal sans être passée par le pipeline de qualification.
            .filter((o) => Boolean(o.phone) && o.status !== "gagne" && o.status !== "perdu")
            .slice(0, NEVER_CALLED_LIMIT)
        ),
    ])
      .then(([buckets, never]) => {
        setData(buckets);
        setNeverCalled(never);
      })
      .catch((e) => setError(e.message));
    api.getMetaCached().then((m) => setTaxonomy(m.qualif_taxonomy)).catch(() => {});
  }, [assigned]);

  // Puce « dernière issue » : batch sur les ids visibles, jamais persisté.
  useEffect(() => {
    if (!data) return;
    const ids = [
      ...data.en_retard,
      ...data.aujourdhui,
      ...data.cette_semaine,
      ...(neverCalled ?? []),
    ].map((o) => o.id);
    if (ids.length === 0) return;
    api.getLastIssues(ids).then(setLastIssues).catch(() => {});
  }, [data, neverCalled]);

  const refreshLastIssue = (id: number) => {
    api.getLastIssues([id]).then((r) => setLastIssues((prev) => ({ ...prev, ...r })));
  };

  if (error) return <ErrorState message={error} />;

  const dueTotal = data
    ? data.en_retard.length + data.aujourdhui.length + data.cette_semaine.length
    : 0;
  const total = dueTotal + (neverCalled?.length ?? 0);

  const populationLabel = POPULATION_LABELS[CLOSER_POPULATION] ?? CLOSER_POPULATION;

  return (
    <>
      <PageHeader
        title="Mes appels"
        subtitle={
          data
            ? `${total} fiche(s) à traiter · ${populationLabel}`
            : "Chargement…"
        }
      >
        {/* Visible seulement si loggé (soft-auth). Défaut = mes appels ; ce
            bouton élargit/rétrécit à toute l'équipe. */}
        {user && (
          <button
            onClick={() => setAssigned((a) => (a === "me" ? "" : "me"))}
            className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
              assigned === "me"
                ? "border-brand-300 bg-brand-50 text-brand-700"
                : "border-slate-200 text-slate-600 hover:bg-slate-50"
            }`}
          >
            {assigned === "me" ? "Toute l'équipe" : "Mes appels"}
          </button>
        )}
      </PageHeader>
      <div className="space-y-6 p-8">
        {!data ? (
          <Loading />
        ) : total === 0 ? (
          <EmptyState label="Rien à relancer pour le moment." />
        ) : (
          <FollowUpLists
            data={data}
            neverCalled={neverCalled ?? []}
            dueTotal={dueTotal}
            lastIssues={lastIssues}
            taxonomy={taxonomy}
            onQualified={refreshLastIssue}
          />
        )}
      </div>
    </>
  );
}

// Extrait en composant séparé (au lieu d'une variable calculée au niveau de la
// page) : `data` y est non-nullable — évite un `data!` qui casse le pré-rendu
// statique (le hook `!data ?` du parent garantit déjà l'appel uniquement une
// fois les données chargées).
function FollowUpLists({
  data,
  neverCalled,
  dueTotal,
  lastIssues,
  taxonomy,
  onQualified,
}: {
  data: FollowUpBuckets;
  neverCalled: OpportunityList[];
  dueTotal: number;
  lastIssues: Record<number, LastIssue>;
  taxonomy: QualifTaxonomy | null;
  onQualified: (id: number) => void;
}) {
  const dueBlock = (
    <>
      {SECTIONS.map(({ key, label, accent }) => {
        const rows = data[key];
        if (rows.length === 0) return null;
        return (
          <div key={key} className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
              <h2 className={`text-sm font-semibold ${accent}`}>{label}</h2>
              <span className="text-xs tabular-nums text-slate-400">{rows.length}</span>
            </div>
            <ul className="divide-y divide-slate-100">
              {rows.map((o) => (
                <FollowUpRow
                  key={o.id}
                  o={o}
                  lastIssue={lastIssues[o.id]}
                  taxonomy={taxonomy}
                  onQualified={() => onQualified(o.id)}
                />
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );

  const neverCalledBlock = neverCalled.length > 0 && (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
        <h2 className="text-sm font-semibold text-slate-700">Jamais appelés</h2>
        <span className="text-xs tabular-nums text-slate-400">{neverCalled.length}</span>
      </div>
      <ul className="divide-y divide-slate-100">
        {neverCalled.map((o) => (
          <FollowUpRow
            key={o.id}
            o={o}
            lastIssue={lastIssues[o.id]}
            taxonomy={taxonomy}
            onQualified={() => onQualified(o.id)}
          />
        ))}
      </ul>
    </div>
  );

  // Rien de dû aujourd'hui : « Jamais appelés » remonte en tête pour garder le
  // closer en mouvement (revue produit — filet auparavant caché tout en bas).
  if (dueTotal === 0) return <>{neverCalledBlock}</>;
  return (
    <>
      {dueBlock}
      {neverCalledBlock}
    </>
  );
}

function FollowUpRow({
  o,
  lastIssue,
  taxonomy,
  onQualified,
}: {
  o: OpportunityList;
  lastIssue?: LastIssue;
  taxonomy: QualifTaxonomy | null;
  onQualified: () => void;
}) {
  // « Stretched link » : le <Link> couvre toute la ligne en overlay absolu, ce
  // qui laisse le numéro être un vrai <a href="tel:"> indépendant (impossible
  // d'imbriquer deux <a>). Le closer compose en un tap sans ouvrir la fiche.
  return (
    <div className="relative flex items-center justify-between gap-4 px-5 py-3 hover:bg-slate-50">
      <Link
        href={`/opportunities/${o.id}`}
        aria-label={o.establishment_name}
        className="absolute inset-0 z-0"
      />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <p className="truncate text-sm font-medium text-slate-900">{o.establishment_name}</p>
          {lastIssue && <IssueBadge issue={lastIssue.issue} raison={lastIssue.raison} />}
        </div>
        <p className="truncate text-xs text-slate-500">
          {o.city}
          {o.next_action ? ` · ${o.next_action}` : ""}
        </p>
      </div>
      <div className="relative z-10 flex shrink-0 items-center gap-3 text-xs text-slate-400">
        {taxonomy && (
          <QuickQualifyPopover
            opportunityId={o.id}
            taxonomy={taxonomy}
            phone={o.phone}
            onAdded={onQualified}
          />
        )}
        {o.phone && (
          <a
            href={`tel:${o.phone.replace(/\s/g, "")}`}
            className="hidden items-center gap-1 text-slate-500 hover:text-brand-600 sm:inline-flex"
          >
            <Phone size={12} /> {o.phone}
          </a>
        )}
        <span className="pointer-events-none tabular-nums">{formatDueLabel(o.next_follow_up_date)}</span>
        <ChevronRight size={14} className="pointer-events-none" />
      </div>
    </div>
  );
}
