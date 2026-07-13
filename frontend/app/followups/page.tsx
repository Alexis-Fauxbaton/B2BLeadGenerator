"use client";

// Vue « À relancer » : trois sections (en retard / aujourd'hui / cette
// semaine), lignes cliquables vers la fiche — c'est par là que les closers
// démarrent leur journée. Sobre : pas de filtre, pas de tableau, juste la
// liste triée par urgence (héritée du backend).

import { useEffect, useState } from "react";
import Link from "next/link";
import { Phone, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { FollowUpBuckets, OpportunityList } from "@/lib/types";
import { formatDueLabel } from "@/lib/labels";
import PageHeader from "@/components/PageHeader";
import { Loading, ErrorState, EmptyState } from "@/components/States";

const SECTIONS: { key: keyof FollowUpBuckets; label: string; accent: string }[] = [
  { key: "en_retard", label: "En retard", accent: "text-rose-600" },
  { key: "aujourdhui", label: "Aujourd'hui", accent: "text-amber-600" },
  { key: "cette_semaine", label: "Cette semaine", accent: "text-slate-700" },
];

export default function FollowUpsPage() {
  const { user } = useAuth();
  const [assigned, setAssigned] = useState<string>("");
  const [data, setData] = useState<FollowUpBuckets | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api
      .getFollowUps(undefined, assigned || undefined)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [assigned]);

  if (error) return <ErrorState message={error} />;

  const total = data
    ? data.en_retard.length + data.aujourdhui.length + data.cette_semaine.length
    : 0;

  return (
    <>
      <PageHeader
        title="À relancer"
        subtitle={data ? `${total} fiche(s) à traiter` : "Chargement…"}
      >
        {/* « Mes relances » : visible seulement si loggé. */}
        {user && (
          <button
            onClick={() => setAssigned((a) => (a === "me" ? "" : "me"))}
            className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
              assigned === "me"
                ? "border-brand-300 bg-brand-50 text-brand-700"
                : "border-slate-200 text-slate-600 hover:bg-slate-50"
            }`}
          >
            Mes relances
          </button>
        )}
      </PageHeader>
      <div className="space-y-6 p-8">
        {!data ? (
          <Loading />
        ) : total === 0 ? (
          <EmptyState label="Rien à relancer pour le moment." />
        ) : (
          SECTIONS.map(({ key, label, accent }) => {
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
                    <FollowUpRow key={o.id} o={o} />
                  ))}
                </ul>
              </div>
            );
          })
        )}
      </div>
    </>
  );
}

function FollowUpRow({ o }: { o: OpportunityList }) {
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
        <p className="truncate text-sm font-medium text-slate-900">{o.establishment_name}</p>
        <p className="truncate text-xs text-slate-500">
          {o.city}
          {o.next_action ? ` · ${o.next_action}` : ""}
        </p>
      </div>
      <div className="relative z-10 flex shrink-0 items-center gap-3 text-xs text-slate-400">
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
