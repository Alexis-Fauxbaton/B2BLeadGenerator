"use client";

// Vue PATRON /activite : journal global des activités des closers du jour,
// filtrable par auteur et par jour (hier/aujourd'hui/date libre), + compteurs
// par closer en tuiles sobres. Admin SOFT (cf. Sidebar / require_admin_soft).

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ActivityJournal } from "@/lib/types";
import { ACTIVITY_TYPE_LABELS, formatRelativeDate } from "@/lib/labels";
import PageHeader from "@/components/PageHeader";
import { Loading, EmptyState } from "@/components/States";
import QualifResults from "@/components/QualifResults";

function isoDay(offsetDays = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const TODAY = isoDay();
const YESTERDAY = isoDay(-1);

export default function ActivitePage() {
  const { user, loading: authLoading } = useAuth();
  const [tab, setTab] = useState<"journal" | "resultats">("journal");
  const [day, setDay] = useState(TODAY);
  const [author, setAuthor] = useState("");
  const [data, setData] = useState<ActivityJournal | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Admin SOFT : ouvert tant que personne n'est loggé (Alexis aujourd'hui).
  const isAdminSoft = !user || user.role === "admin";

  useEffect(() => {
    if (!isAdminSoft || tab !== "journal") return;
    setData(null);
    api
      .getActivite({ day, author: author || undefined })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [day, author, isAdminSoft, tab]);

  if (authLoading) return <Loading />;

  if (!isAdminSoft) {
    return (
      <div className="mx-8 my-10 rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
        Réservé à l&apos;administrateur.
      </div>
    );
  }

  if (tab === "journal" && error) {
    return (
      <div className="mx-8 my-10 rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">
        {error}
      </div>
    );
  }

  const dayLabel = day === TODAY ? "aujourd'hui" : day === YESTERDAY ? "hier" : day;

  return (
    <>
      <PageHeader
        title="Activité"
        subtitle={
          tab === "journal"
            ? data
              ? `${data.activities.length} activité(s) — ${dayLabel}`
              : "Chargement…"
            : "Monitoring des résultats de qualification"
        }
      >
        {/* Toggle Journal | Résultats — zéro nouvelle page dans la nav. */}
        <div className="inline-flex rounded-lg border border-slate-200 p-0.5">
          <button
            onClick={() => setTab("journal")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === "journal" ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            Journal
          </button>
          <button
            onClick={() => setTab("resultats")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === "resultats" ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            Résultats
          </button>
        </div>
      </PageHeader>
      {tab === "resultats" ? (
        <div className="p-8">
          <QualifResults />
        </div>
      ) : (
      <div className="space-y-6 p-8">
        {/* Compteurs par closer (journée entière, tous auteurs). */}
        {data && data.counts.length > 0 && (
          <div className="flex flex-wrap gap-3">
            {data.counts.map((c) => (
              <div
                key={c.author ?? "—"}
                className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-card"
              >
                <p className="text-xs text-slate-400">{c.author ?? "Sans auteur"}</p>
                <p className="text-xl font-semibold tabular-nums text-slate-900">{c.count}</p>
              </div>
            ))}
          </div>
        )}

        {/* Filtres : jour + auteur */}
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card">
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setDay(YESTERDAY)}
              className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
                day === YESTERDAY
                  ? "border-brand-300 bg-brand-50 text-brand-700"
                  : "border-slate-200 text-slate-600 hover:bg-slate-50"
              }`}
            >
              Hier
            </button>
            <button
              onClick={() => setDay(TODAY)}
              className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
                day === TODAY
                  ? "border-brand-300 bg-brand-50 text-brand-700"
                  : "border-slate-200 text-slate-600 hover:bg-slate-50"
              }`}
            >
              Aujourd&apos;hui
            </button>
            <input
              type="date"
              value={day}
              onChange={(e) => setDay(e.target.value)}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </div>
          <select
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
          >
            <option value="">Tous les auteurs</option>
            {data?.counts
              .filter((c) => c.author)
              .map((c) => (
                <option key={c.author} value={c.author as string}>
                  {c.author}
                </option>
              ))}
          </select>
        </div>

        {/* Journal */}
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          {!data ? (
            <Loading />
          ) : data.activities.length === 0 ? (
            <EmptyState label="Aucune activité pour ce jour." />
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.activities.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-4 px-5 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-900">
                      {a.opportunity_name ?? `#${a.opportunity_id}`}
                    </p>
                    <p className="truncate text-xs text-slate-500">
                      {ACTIVITY_TYPE_LABELS[a.type] ?? a.type}
                      {a.note ? ` · ${a.note}` : ""}
                    </p>
                  </div>
                  <div className="shrink-0 text-right text-xs text-slate-400">
                    <p>{a.author ?? "—"}</p>
                    <p>{formatRelativeDate(a.created_at)}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      )}
    </>
  );
}
