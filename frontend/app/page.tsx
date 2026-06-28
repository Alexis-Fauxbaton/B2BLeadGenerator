"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Target,
  Flame,
  PhoneOff,
  BellRing,
  ThumbsUp,
  CalendarCheck,
  Trophy,
  ArrowRight,
} from "lucide-react";
import { api } from "@/lib/api";
import type { DashboardStats } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/labels";
import PageHeader from "@/components/PageHeader";
import StatCard from "@/components/StatCard";
import { ScoreBadge, SignalBadge, StatusBadge, ChannelBadge } from "@/components/Badges";
import { Loading, ErrorState } from "@/components/States";

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getDashboard().then(setStats).catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!stats)
    return (
      <>
        <PageHeader title="Dashboard" />
        <Loading />
      </>
    );

  const maxSignal = Math.max(1, ...stats.by_signal.map((s) => s.count));

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Vue d'ensemble de vos opportunités CHR"
      />

      <div className="space-y-6 p-8">
        {/* Cards */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard label="Total opportunités" value={stats.total_opportunities} icon={Target} accent="brand" />
          <StatCard label="Leads chauds" value={stats.hot_leads} icon={Flame} accent="rose" hint="Score ≥ 8" />
          <StatCard label="À contacter" value={stats.not_contacted} icon={PhoneOff} accent="slate" />
          <StatCard label="Relances dues" value={stats.follow_ups_due} icon={BellRing} accent="amber" />
          <StatCard label="Intéressés" value={stats.interested} icon={ThumbsUp} accent="violet" />
          <StatCard label="RDV" value={stats.appointments} icon={CalendarCheck} accent="cyan" />
          <StatCard label="Gagnés" value={stats.won} icon={Trophy} accent="emerald" />
          <StatCard label="Perdus" value={stats.lost} icon={PhoneOff} accent="slate" />
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Répartition par signal */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card lg:col-span-2">
            <h2 className="text-sm font-semibold text-slate-900">Répartition par signal principal</h2>
            <div className="mt-4 space-y-3">
              {stats.by_signal.map((s) => (
                <div key={s.label} className="flex items-center gap-3">
                  <span className="w-44 shrink-0 truncate text-sm text-slate-600">{s.label}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-brand-500"
                      style={{ width: `${(s.count / maxSignal) * 100}%` }}
                    />
                  </div>
                  <span className="w-6 text-right text-sm font-medium text-slate-700">{s.count}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Répartition par statut */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
            <h2 className="text-sm font-semibold text-slate-900">Répartition par statut</h2>
            <div className="mt-4 space-y-2">
              {stats.by_status.map((s) => (
                <div key={s.label} className="flex items-center justify-between">
                  <StatusBadge status={s.label} />
                  <span className="text-sm font-medium text-slate-700">{s.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Top 5 hottest */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-card">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
            <h2 className="text-sm font-semibold text-slate-900">
              🔥 Top 5 des opportunités les plus chaudes
            </h2>
            <Link
              href="/opportunities"
              className="flex items-center gap-1 text-sm font-medium text-brand-600 hover:text-brand-700"
            >
              Tout voir <ArrowRight size={14} />
            </Link>
          </div>
          <div className="divide-y divide-slate-100">
            {stats.hottest.map((o) => (
              <Link
                key={o.id}
                href={`/opportunities/${o.id}`}
                className="flex items-center justify-between px-5 py-3 hover:bg-slate-50"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-900">{o.establishment_name}</span>
                    <span className="text-xs text-slate-400">
                      {o.establishment_type} · {o.city}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <SignalBadge label={o.main_signal} />
                    <ChannelBadge channel={o.recommended_channel} />
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <ScoreBadge score={o.opportunity_score} />
                  <ArrowRight size={16} className="text-slate-300" />
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
