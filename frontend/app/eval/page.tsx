"use client";

import { useEffect, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { GroundtruthResult } from "@/lib/types";
import { EVAL_LABEL_LABELS, EVAL_LABEL_STYLES, formatDate } from "@/lib/labels";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface EvalRow {
  handle: string;
  name: string;
  true_label: string;
  true_bucket: string;
  predicted_bucket: string | null;
  confidence: string;
  provenance: string;
  rationale: string;
  ig_url: string;
  false_positive: boolean;
  missed_opening: boolean;
  has_snapshot: boolean;
}

interface EvalResult {
  generated_at: string;
  precision_a_contacter: number | null;
  recall_opening: number | null;
  n: number;
  n_a_contacter: number;
  tp_opening: number;
  n_opening: number;
  rows: EvalRow[];
}

// Libellés des labels vérité + couleurs.
const LABEL_STYLE: Record<string, string> = {
  opening: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  just_opened: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  established: "bg-slate-100 text-slate-600 ring-slate-200",
  chain_multisite: "bg-violet-50 text-violet-700 ring-violet-200",
  not_venue: "bg-rose-50 text-rose-700 ring-rose-200",
  noise: "bg-amber-50 text-amber-700 ring-amber-200",
};

const BUCKET_STYLE: Record<string, string> = {
  a_contacter: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  a_surveiller: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  a_confirmer: "bg-amber-50 text-amber-700 ring-amber-200",
  a_reverifier: "bg-amber-50 text-amber-700 ring-amber-200",
  ecarte: "bg-slate-100 text-slate-600 ring-slate-200",
};

function Badge({ text, cls }: { text: string; cls: string }) {
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}
    >
      {text}
    </span>
  );
}

// Verdict de comparaison vérité vs inféré.
function verdict(row: EvalRow): { label: string; cls: string } {
  if (!row.has_snapshot || row.predicted_bucket === null)
    return { label: "—", cls: "text-slate-400" };
  if (row.false_positive)
    return { label: "✗ faux positif", cls: "text-rose-600 font-semibold" };
  if (row.missed_opening)
    return { label: "⚠ ouverture ratée", cls: "text-amber-600 font-semibold" };
  if (row.true_label === "opening" && row.predicted_bucket === "a_contacter")
    return { label: "✓ juste", cls: "text-emerald-600 font-semibold" };
  return { label: "✓ écarté (ok)", cls: "text-emerald-600" };
}

export default function EvalPage() {
  const [data, setData] = useState<EvalResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load(refresh = false) {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_URL}/api/eval/instagram${refresh ? "?refresh=true" : ""}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erreur de chargement");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const pct = (x: number | null) => (x === null ? "n/a" : `${Math.round(x * 100)}%`);

  return (
    <div className="p-8 max-w-6xl">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Éval — leads Instagram</h1>
          <p className="mt-1 text-sm text-slate-500">
            Jeu de vérité annoté à la main vs classification du pipeline. Statut
            véridique vs statut inféré, par compte — clique un handle pour vérifier
            toi-même sur Instagram.
          </p>
        </div>
        <button
          onClick={() => load(true)}
          disabled={refreshing}
          className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw size={15} className={refreshing ? "animate-spin" : ""} />
          Recalculer
        </button>
      </div>

      {loading && <p className="mt-8 text-sm text-slate-500">Chargement…</p>}
      {error && (
        <p className="mt-8 text-sm text-rose-600">
          Erreur : {error}. Le backend tourne-t-il ? (snapshots requis — sinon lancer
          <code className="mx-1 rounded bg-slate-100 px-1">--snapshot</code>)
        </p>
      )}

      {data && (
        <>
          {/* Métriques clés */}
          <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Metric
              label="Précision a_contacter"
              value={pct(data.precision_a_contacter)}
              sub={`${data.tp_opening} opening / ${data.n_a_contacter} classés`}
              accent="text-brand-700"
            />
            <Metric
              label="Rappel opening"
              value={pct(data.recall_opening)}
              sub={`${data.tp_opening} / ${data.n_opening} retrouvés`}
            />
            <Metric label="Comptes évalués" value={String(data.n)} sub="jeu de vérité" />
            <Metric
              label="Faux positifs"
              value={String(data.rows.filter((r) => r.false_positive).length)}
              sub="classés a_contacter à tort"
              accent="text-rose-600"
            />
          </div>

          {/* Tableau */}
          <div className="mt-6 overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-3 font-medium">Compte</th>
                  <th className="px-4 py-3 font-medium">Statut véridique</th>
                  <th className="px-4 py-3 font-medium">Statut inféré</th>
                  <th className="px-4 py-3 font-medium">Verdict</th>
                  <th className="px-4 py-3 font-medium">Justification (annotation)</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => {
                  const v = verdict(row);
                  return (
                    <tr key={row.handle} className="border-b border-slate-100 last:border-0 align-top">
                      <td className="px-4 py-3">
                        <a
                          href={row.ig_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 font-medium text-brand-700 hover:underline"
                        >
                          @{row.handle}
                          <ExternalLink size={12} />
                        </a>
                        <div className="text-xs text-slate-400">{row.name}</div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          text={row.true_label}
                          cls={LABEL_STYLE[row.true_label] || "bg-slate-100 text-slate-600 ring-slate-200"}
                        />
                        <div className="mt-1 text-xs text-slate-400">→ {row.true_bucket}</div>
                      </td>
                      <td className="px-4 py-3">
                        {row.predicted_bucket ? (
                          <Badge
                            text={row.predicted_bucket}
                            cls={BUCKET_STYLE[row.predicted_bucket] || "bg-slate-100 text-slate-600 ring-slate-200"}
                          />
                        ) : (
                          <span className="text-xs text-slate-400">pas de snapshot</span>
                        )}
                      </td>
                      <td className={`px-4 py-3 text-sm ${v.cls}`}>{v.label}</td>
                      <td className="px-4 py-3 text-xs text-slate-500">
                        {row.rationale}
                        <div className="mt-1 text-slate-300">
                          conf. {row.confidence} · {row.provenance}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="mt-3 text-xs text-slate-400">
            Généré {new Date(data.generated_at).toLocaleString("fr-FR")} · « statut inféré »
            = décision actuelle du pipeline (a_contacter / ecarte). La couche buckets fine
            (a_surveiller / a_confirmer / a_reverifier) viendra ensuite, réglée sur ce jeu.
          </p>
        </>
      )}

      <GroundtruthSection />
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${accent || "text-slate-900"}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

function GroundtruthSection() {
  const [gt, setGt] = useState<GroundtruthResult | null>(null);
  const [asOf, setAsOf] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getGroundtruth(asOf || undefined)
      .then((res) => {
        if (!cancelled) setGt(res);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Erreur de chargement");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [asOf]);

  const effective = gt?.as_of ?? null;
  const effectiveLabel = effective
    ? ` au ${new Date(effective).toLocaleDateString("fr-FR")}`
    : "";

  return (
    <section className="mt-12 border-t border-slate-200 pt-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Jeu de preuve</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Le jeu de vérité annoté tel qu'il existait à une date donnée (journal
            daté, append-only). Filtre par date pour revoir l'état passé ; la
            « prédiction actuelle » vient du dernier résultat d'éval en cache.
          </p>
        </div>
        <label className="flex flex-col gap-1 text-xs font-medium uppercase tracking-wide text-slate-400">
          Au
          <input
            type="date"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-normal normal-case text-slate-700"
          />
        </label>
      </div>

      {loading && <p className="mt-6 text-sm text-slate-500">Chargement…</p>}
      {error && <p className="mt-6 text-sm text-rose-600">Erreur : {error}</p>}

      {gt && !loading && (
        <>
          <p className="mt-4 text-sm font-medium text-slate-600">
            {gt.total} compte{gt.total > 1 ? "s" : ""}
            {effectiveLabel}
          </p>
          <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-3 font-medium">Compte</th>
                  <th className="px-4 py-3 font-medium">Label vérité</th>
                  <th className="px-4 py-3 font-medium">Confiance</th>
                  <th className="px-4 py-3 font-medium">Prédiction actuelle</th>
                  <th className="px-4 py-3 font-medium">Annoté le</th>
                  <th className="px-4 py-3 font-medium">Justification</th>
                </tr>
              </thead>
              <tbody>
                {gt.rows.map((row) => (
                  <tr
                    key={row.handle}
                    className={`border-b border-slate-100 align-top last:border-0 ${
                      row.disagreement ? "bg-rose-50/60" : ""
                    }`}
                  >
                    <td className="px-4 py-3">
                      <a
                        href={row.ig_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 font-medium text-brand-700 hover:underline"
                      >
                        @{row.handle}
                        <ExternalLink size={12} />
                      </a>
                      <div className="text-xs text-slate-400">{row.name}</div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge
                        text={EVAL_LABEL_LABELS[row.label] || row.label}
                        cls={EVAL_LABEL_STYLES[row.label] || "bg-slate-100 text-slate-600 ring-slate-200"}
                      />
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">{row.confidence || "—"}</td>
                    <td className="px-4 py-3">
                      {row.predicted ? (
                        <Badge
                          text={EVAL_LABEL_LABELS[row.predicted] || row.predicted}
                          cls={EVAL_LABEL_STYLES[row.predicted] || "bg-slate-100 text-slate-600 ring-slate-200"}
                        />
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                      {row.disagreement && (
                        <div className="mt-1 text-xs font-semibold text-rose-600">désaccord</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">{formatDate(row.annotated_at)}</td>
                    <td className="px-4 py-3 text-xs text-slate-500">{row.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
