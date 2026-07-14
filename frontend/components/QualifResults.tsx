"use client";

// Onglet « Résultats » de /activite (monitoring des qualifications, patron).
// 100% lecture agrégée — aucune action, aucun bouton d'écriture sur cet écran
// (invariant du design qualification §2). Sobre : tuiles de KPIs + deux petits
// tableaux + deux mini-graphiques, pas de bibliothèque de charts (cohérent
// avec le style déjà utilisé sur le dashboard).

import { useEffect, useState } from "react";
import { Target, Percent, Phone, MessageSquare } from "lucide-react";
import { api } from "@/lib/api";
import type { QualifStats } from "@/lib/types";
import {
  ACTIVITY_TYPE_LABELS,
  QUALIF_RAISON_LABELS,
  formatDate,
} from "@/lib/labels";
import StatCard from "@/components/StatCard";
import { Loading, EmptyState } from "@/components/States";

type Preset = "today" | "7j" | "30j";

const PRESETS: { key: Preset; label: string }[] = [
  { key: "today", label: "Aujourd'hui" },
  { key: "7j", label: "7 jours" },
  { key: "30j", label: "30 jours" },
];

function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)} %`;
}

export default function QualifResults() {
  const [preset, setPreset] = useState<Preset>("today");
  const [customOpen, setCustomOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [data, setData] = useState<QualifStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    const params = customOpen && start && end ? { start, end } : { period: preset };
    api
      .getActivityStats(params)
      .then(setData)
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, customOpen, start, end]);

  if (error) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Période */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card">
        <div className="flex items-center gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              onClick={() => {
                setPreset(p.key);
                setCustomOpen(false);
              }}
              className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
                !customOpen && preset === p.key
                  ? "border-brand-300 bg-brand-50 text-brand-700"
                  : "border-slate-200 text-slate-600 hover:bg-slate-50"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button
          onClick={() => setCustomOpen((v) => !v)}
          className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
            customOpen
              ? "border-brand-300 bg-brand-50 text-brand-700"
              : "border-slate-200 text-slate-600 hover:bg-slate-50"
          }`}
        >
          Dates libres
        </button>
        {customOpen && (
          <div className="flex items-center gap-2">
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
            <span className="text-sm text-slate-400">→</span>
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </div>
        )}
      </div>

      {!data ? (
        <Loading />
      ) : (
        <>
          <p className="text-xs text-slate-400">
            {formatDate(data.period_start)} — {formatDate(data.period_end)}
          </p>

          {/* KPIs */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard label="Tentatives" value={data.kpis.tentatives} icon={Target} accent="brand" />
            <StatCard
              label="Joignabilité"
              value={pct(data.kpis.joignabilite)}
              icon={Percent}
              accent="emerald"
              hint="joint / (joint + pas joint + KO)"
            />
            <StatCard label="Volume d'appels" value={data.kpis.volume_appels} icon={Phone} accent="cyan" />
            <StatCard
              label="Réponses email + DM"
              value={data.kpis.reponses_email_dm}
              icon={MessageSquare}
              accent="violet"
            />
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Par closer */}
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
              <h2 className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-900">
                Par closer
              </h2>
              {data.by_closer.length === 0 ? (
                <EmptyState label="Aucune tentative sur la période." />
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-slate-400">
                      <th className="px-5 py-2 font-medium">Closer</th>
                      <th className="px-3 py-2 text-right font-medium">Tentatives</th>
                      <th className="px-3 py-2 text-right font-medium">Joints</th>
                      <th className="px-5 py-2 text-right font-medium">Joignabilité</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {data.by_closer.map((c) => (
                      <tr key={c.closer ?? "—"}>
                        <td className="px-5 py-2 font-medium text-slate-700">
                          {c.closer ?? "Sans auteur"}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                          {c.tentatives}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">{c.joints}</td>
                        <td className="px-5 py-2 text-right tabular-nums font-medium text-slate-900">
                          {pct(c.joignabilite)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* Par canal */}
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
              <h2 className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-900">
                Par canal
              </h2>
              {data.by_channel.length === 0 ? (
                <EmptyState label="Aucune tentative sur la période." />
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-slate-400">
                      <th className="px-5 py-2 font-medium">Canal</th>
                      <th className="px-3 py-2 text-right font-medium">Tentatives</th>
                      <th className="px-3 py-2 text-right font-medium">Joints</th>
                      <th className="px-5 py-2 text-right font-medium">Joignabilité</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {data.by_channel.map((c) => (
                      <tr key={c.type}>
                        <td className="px-5 py-2 font-medium text-slate-700">
                          {ACTIVITY_TYPE_LABELS[c.type] ?? c.type}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                          {c.tentatives}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">{c.joints}</td>
                        <td className="px-5 py-2 text-right tabular-nums font-medium text-slate-900">
                          {pct(c.joignabilite)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Top raisons de KO */}
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
              <h2 className="text-sm font-semibold text-slate-900">Top raisons de KO</h2>
              {data.top_ko_reasons.length === 0 ? (
                <p className="mt-4 text-sm text-slate-400">Aucun KO sur la période.</p>
              ) : (
                <div className="mt-4 space-y-3">
                  {(() => {
                    const max = Math.max(1, ...data.top_ko_reasons.map((r) => r.count));
                    return data.top_ko_reasons.map((r) => (
                      <div key={r.raison} className="flex items-center gap-3">
                        <span className="w-36 shrink-0 truncate text-sm text-slate-600">
                          {QUALIF_RAISON_LABELS[r.raison] ?? r.raison}
                        </span>
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full bg-rose-400"
                            style={{ width: `${(r.count / max) * 100}%` }}
                          />
                        </div>
                        <span className="w-6 text-right text-sm font-medium text-slate-700">
                          {r.count}
                        </span>
                      </div>
                    ));
                  })()}
                </div>
              )}
            </div>

            {/* Volume d'appels par jour */}
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
              <h2 className="text-sm font-semibold text-slate-900">Volume d'appels par jour</h2>
              {data.daily_call_volume.every((d) => d.count === 0) ? (
                <p className="mt-4 text-sm text-slate-400">Aucun appel sur la période.</p>
              ) : (
                <div className="mt-4 flex h-24 items-end gap-1">
                  {(() => {
                    const max = Math.max(1, ...data.daily_call_volume.map((d) => d.count));
                    return data.daily_call_volume.map((d) => (
                      <div
                        key={d.day}
                        className="group relative flex-1"
                        title={`${formatDate(d.day)} · ${d.count} appel(s)`}
                      >
                        <div
                          className="w-full rounded-t bg-brand-400 group-hover:bg-brand-500"
                          style={{ height: `${Math.max(4, (d.count / max) * 96)}px` }}
                        />
                      </div>
                    ));
                  })()}
                </div>
              )}
              {data.daily_call_volume.length > 1 && (
                <div className="mt-1 flex justify-between text-[11px] text-slate-400">
                  <span>{formatDate(data.daily_call_volume[0].day)}</span>
                  <span>{formatDate(data.daily_call_volume[data.daily_call_volume.length - 1].day)}</span>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
