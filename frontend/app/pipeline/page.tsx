"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Pipeline } from "@/lib/types";
import { STATUS_LABELS, STATUS_ORDER, formatDate } from "@/lib/labels";
import { ChannelBadge, ScoreBadge } from "@/components/Badges";
import PageHeader from "@/components/PageHeader";
import { Loading, ErrorState } from "@/components/States";

const COLUMN_ACCENT: Record<string, string> = {
  non_contacte: "border-t-slate-400",
  contacte: "border-t-blue-400",
  relance: "border-t-amber-400",
  interesse: "border-t-violet-400",
  rdv: "border-t-cyan-400",
  gagne: "border-t-emerald-400",
  perdu: "border-t-rose-400",
};

export default function PipelinePage() {
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const reload = () =>
    api.getPipeline().then(setPipeline).catch((e) => setError(e.message));

  useEffect(() => {
    reload();
  }, []);

  const move = async (id: number, status: string) => {
    setBusy(id);
    try {
      await api.updateStatus(id, { status });
      await reload();
    } finally {
      setBusy(null);
    }
  };

  if (error) return <ErrorState message={error} />;

  return (
    <>
      <PageHeader title="Pipeline" subtitle="Suivi commercial — vue kanban" />

      <div className="p-8">
        {!pipeline ? (
          <Loading />
        ) : (
          <div className="flex gap-4 overflow-x-auto scrollbar-thin pb-4">
            {STATUS_ORDER.map((status) => {
              const cards = pipeline[status] ?? [];
              return (
                <div
                  key={status}
                  className={`flex w-72 shrink-0 flex-col rounded-xl border border-t-4 border-slate-200 bg-slate-100/60 ${COLUMN_ACCENT[status]}`}
                >
                  <div className="flex items-center justify-between px-3 py-2.5">
                    <span className="text-sm font-semibold text-slate-700">
                      {STATUS_LABELS[status]}
                    </span>
                    <span className="rounded-full bg-white px-2 py-0.5 text-xs font-medium text-slate-500">
                      {cards.length}
                    </span>
                  </div>

                  <div className="flex-1 space-y-2 overflow-y-auto px-2 pb-2">
                    {cards.map((o) => (
                      <div
                        key={o.id}
                        className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm"
                      >
                        <Link
                          href={`/opportunities/${o.id}`}
                          className="text-sm font-medium text-slate-900 hover:text-brand-700"
                        >
                          {o.establishment_name}
                        </Link>
                        <p className="text-xs text-slate-400">{o.city}</p>
                        <p className="mt-1 text-xs text-slate-500">{o.main_signal}</p>
                        <div className="mt-2 flex items-center justify-between">
                          <ScoreBadge score={o.opportunity_score} />
                          <ChannelBadge channel={o.recommended_channel} />
                        </div>
                        {o.next_follow_up_date && (
                          <p className="mt-2 text-xs text-amber-600">
                            ⏰ Relance {formatDate(o.next_follow_up_date)}
                          </p>
                        )}
                        <select
                          value={status}
                          disabled={busy === o.id}
                          onChange={(e) => move(o.id, e.target.value)}
                          className="mt-2 w-full rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600 focus:outline-none"
                        >
                          {STATUS_ORDER.map((s) => (
                            <option key={s} value={s}>
                              Déplacer → {STATUS_LABELS[s]}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                    {cards.length === 0 && (
                      <p className="px-2 py-6 text-center text-xs text-slate-400">Vide</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
