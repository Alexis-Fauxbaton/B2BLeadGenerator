"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Search,
  SlidersHorizontal,
  ArrowUpDown,
  DownloadCloud,
  Loader2,
  Phone,
  Mail,
  Instagram,
  Globe,
  User,
} from "lucide-react";
import { api, type OpportunityFilters } from "@/lib/api";
import type { IngestStats, Meta, OpportunityList } from "@/lib/types";
import {
  CHANNEL_LABELS,
  LIFECYCLE_LABEL_LABELS,
  LIFECYCLE_LABEL_ORDER,
  LIFECYCLE_LABEL_ORDER_ARCHI,
  LIFECYCLE_LABEL_ORDER_CHR,
  STATUS_LABELS,
  formatDate,
  formatFollowers,
} from "@/lib/labels";
import PageHeader from "@/components/PageHeader";
import {
  ScoreBadge,
  SignalBadge,
  SourceBadge,
  PopulationBadge,
  StatusBadge,
  StageBadge,
  HeatBadge,
  LifecycleBadge,
} from "@/components/Badges";
import { Loading, ErrorState, EmptyState } from "@/components/States";

const SELECT_CLS =
  "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100";

function ContactIcons({ o }: { o: OpportunityList }) {
  // Précision d'abord : un canal n'est "allumé" que si le match est géo-confirmé
  // (haute) — sinon il reste gris ("à trouver").
  const estabTrusted = o.contact_confidence === "haute";
  const instaOn = Boolean(o.instagram) && (estabTrusted || o.source === "instagram");
  const items: [boolean, typeof Phone, string][] = [
    [Boolean(o.phone) && estabTrusted, Phone, "Téléphone"],
    [Boolean(o.email) && estabTrusted, Mail, "Email"],
    [Boolean(o.website) && estabTrusted, Globe, "Site web"],
    [o.decision_maker_confidence === "haute", User, "Décideur"],
  ];
  const followers = formatFollowers(o.followers_count);
  if (!instaOn && !items.some(([on]) => on))
    return <span className="text-xs text-slate-300">à trouver</span>;
  return (
    <div className="flex items-center gap-1.5">
      <Instagram
        size={15}
        className={instaOn ? "text-emerald-600" : "text-slate-200"}
        aria-label={instaOn ? "Instagram" : "Instagram absent"}
      />
      {/* Abonnés Instagram : "les petits comptes répondent plus souvent" —
          repère à vue d'œil, affiché seulement quand le canal est actif. */}
      {instaOn && followers && (
        <span className="text-xs tabular-nums text-slate-400" title={`${o.followers_count} abonnés Instagram`}>
          {followers}
        </span>
      )}
      {items.map(([on, Icon, label]) => (
        <Icon
          key={label}
          size={15}
          className={on ? "text-emerald-600" : "text-slate-200"}
          aria-label={on ? label : `${label} absent`}
        />
      ))}
    </div>
  );
}

// Défaut produit (pivot 2026-07-10) : la prospection Ambient Home cible les
// architectes — le CHR reste accessible via le sélecteur de population.
const DEFAULT_FILTERS: OpportunityFilters = {
  sort_by: "score",
  order: "desc",
  population: "architecte",
};

// Clé versionnée : l'ancienne clé "opp_filters" pouvait avoir été enregistrée
// avant l'introduction de "population" (donc sans ce champ, ou avec un funnel
// pré-pivot). On repart sur une clé neuve pour que ces vieux navigateurs
// reçoivent bien le nouveau défaut "architecte" plutôt que de le restaurer
// comme "toutes populations" (= CHR visibles).
const OPP_FILTERS_STORAGE_KEY = "opp_filters-v2";

export default function OpportunitiesPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [rows, setRows] = useState<OpportunityList[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [filters, setFilters] = useState<OpportunityFilters>(DEFAULT_FILTERS);
  // Persiste les filtres entre navigations (retour depuis une fiche).
  const [ready, setReady] = useState(false);

  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<IngestStats | null>(null);

  const loadMeta = () =>
    api.getMeta().then(setMeta).catch((e) => setError(e.message));

  const loadRows = () => {
    setRows(null);
    api.getOpportunities(filters).then(setRows).catch((e) => setError(e.message));
  };

  useEffect(() => {
    loadMeta();
  }, []);

  // Restaure les filtres sauvegardés au montage.
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(OPP_FILTERS_STORAGE_KEY);
      if (saved) {
        // Merge défensif : un état sauvegardé qui n'a pas (encore) de clé
        // "population" (ex. sauvegardé juste après une migration) retombe sur
        // le défaut produit plutôt que sur "toutes populations" (= CHR visibles).
        setFilters({ ...DEFAULT_FILTERS, ...JSON.parse(saved) });
      }
    } catch {}
    setReady(true);
  }, []);

  // Recharge + persiste quand les filtres changent (après restauration).
  useEffect(() => {
    if (!ready) return;
    try {
      window.localStorage.setItem(OPP_FILTERS_STORAGE_KEY, JSON.stringify(filters));
    } catch {}
    loadRows();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, ready]);

  const runImport = async () => {
    setImporting(true);
    setImportResult(null);
    try {
      const stats = await api.ingest({ since_days: 60, limit: 100 });
      setImportResult(stats);
      loadMeta();
      loadRows();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setImporting(false);
    }
  };

  const set = (patch: Partial<OpportunityFilters>) =>
    setFilters((f) => ({ ...f, ...patch }));

  const sortLabel = useMemo(
    () =>
      ({
        score: "Score",
        detection_date: "Date de détection",
        city: "Ville",
        status: "Statut",
      }[filters.sort_by ?? "score"]),
    [filters.sort_by]
  );

  if (error) return <ErrorState message={error} />;

  return (
    <>
      <PageHeader
        title="Opportunités"
        subtitle={rows ? `${rows.length} établissement(s)` : "Chargement…"}
      >
        <button
          onClick={runImport}
          disabled={importing}
          className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
          title="Récupérer de vrais leads CHR depuis BODACC (Île-de-France, 60 derniers jours)"
        >
          {importing ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <DownloadCloud size={16} />
          )}
          Importer (BODACC)
        </button>
      </PageHeader>

      <div className="space-y-4 p-8">
        {importResult && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            <span className="font-medium">Import BODACC terminé :</span>
            <span>{importResult.fetched} annonces analysées</span>
            <span>· {importResult.chr_matched} CHR détectés</span>
            <span>· <b>{importResult.created} nouveaux</b></span>
            <span>· {importResult.updated} mis à jour</span>
            <span>· {importResult.skipped_dupes} doublons ignorés</span>
            {importResult.errors > 0 && <span>· {importResult.errors} erreurs</span>}
          </div>
        )}
        {/* Filtres */}
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-700">
            <SlidersHorizontal size={16} className="text-slate-400" /> Filtres
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
            <div className="relative">
              <Search size={16} className="absolute left-3 top-2.5 text-slate-400" />
              <input
                placeholder="Rechercher par nom…"
                className={`${SELECT_CLS} w-full pl-9`}
                value={filters.search ?? ""}
                onChange={(e) => set({ search: e.target.value })}
              />
            </div>

            <select className={SELECT_CLS} value={filters.city ?? ""} onChange={(e) => set({ city: e.target.value })}>
              <option value="">Toutes les villes</option>
              {meta?.cities.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>

            <select className={SELECT_CLS} value={filters.establishment_type ?? ""} onChange={(e) => set({ establishment_type: e.target.value })}>
              <option value="">Tous les types</option>
              {meta?.establishment_types.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>

            <select className={SELECT_CLS} value={filters.main_signal ?? ""} onChange={(e) => set({ main_signal: e.target.value })}>
              <option value="">Tous les signaux</option>
              {meta?.signal_types.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>

            <select className={SELECT_CLS} value={filters.status ?? ""} onChange={(e) => set({ status: e.target.value })}>
              <option value="">Tous les statuts</option>
              {meta?.statuses.map((s) => <option key={s} value={s}>{STATUS_LABELS[s] ?? s}</option>)}
            </select>

            <select className={SELECT_CLS} value={filters.recommended_channel ?? ""} onChange={(e) => set({ recommended_channel: e.target.value })}>
              <option value="">Tous les canaux</option>
              {meta?.channels.map((c) => <option key={c} value={c}>{CHANNEL_LABELS[c] ?? c}</option>)}
            </select>

            <select className={SELECT_CLS} value={filters.source ?? ""} onChange={(e) => set({ source: e.target.value })}>
              <option value="">Toutes les sources</option>
              <option value="demo">Démo</option>
              <option value="bodacc">BODACC (réel)</option>
              <option value="instagram">Instagram</option>
            </select>

            <select className={SELECT_CLS} value={filters.population ?? ""} onChange={(e) => set({ population: e.target.value })}>
              <option value="">Toutes les populations</option>
              <option value="chr">CHR</option>
              <option value="architecte">Architectes</option>
            </select>

            <select className={SELECT_CLS} value={filters.lifecycle_label ?? ""} onChange={(e) => set({ lifecycle_label: e.target.value })}>
              <option value="">Tous les cycles de vie</option>
              {/* Options adaptées à la population sélectionnée (combo croisé = toujours vide). */}
              {(filters.population === "architecte"
                ? LIFECYCLE_LABEL_ORDER_ARCHI
                : filters.population === "chr"
                  ? LIFECYCLE_LABEL_ORDER_CHR
                  : LIFECYCLE_LABEL_ORDER
              ).map((l) => (
                <option key={l} value={l}>{LIFECYCLE_LABEL_LABELS[l]}</option>
              ))}
            </select>

            <select className={SELECT_CLS} value={filters.min_score ?? ""} onChange={(e) => set({ min_score: e.target.value ? Number(e.target.value) : undefined })}>
              <option value="">Score minimum</option>
              {[8, 6, 5, 3].map((v) => <option key={v} value={v}>≥ {v}/10</option>)}
            </select>

            <div className="flex items-center gap-2">
              <select className={`${SELECT_CLS} flex-1`} value={filters.sort_by ?? "score"} onChange={(e) => set({ sort_by: e.target.value })}>
                <option value="score">Tri : Score</option>
                <option value="detection_date">Tri : Détection</option>
                <option value="city">Tri : Ville</option>
                <option value="status">Tri : Statut</option>
              </select>
              <button
                onClick={() => set({ order: filters.order === "desc" ? "asc" : "desc" })}
                className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 hover:bg-slate-50"
                title={`Ordre : ${filters.order === "desc" ? "décroissant" : "croissant"}`}
              >
                <ArrowUpDown size={16} />
              </button>
            </div>
          </div>
        </div>

        {/* Tableau */}
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          {!rows ? (
            <Loading />
          ) : rows.length === 0 ? (
            <EmptyState label="Aucune opportunité ne correspond à ces filtres." />
          ) : (
            <div className="overflow-x-auto scrollbar-thin">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-3">Établissement</th>
                    <th className="px-4 py-3">Type</th>
                    <th className="px-4 py-3">Ville</th>
                    <th className="px-4 py-3">Signal</th>
                    <th className="px-4 py-3">Cycle de vie</th>
                    <th className="px-4 py-3">Score</th>
                    <th className="px-4 py-3">Besoin</th>
                    <th className="px-4 py-3">Contact</th>
                    <th className="px-4 py-3">Statut</th>
                    <th className="px-4 py-3">Détecté</th>
                    <th className="px-4 py-3"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((o) => (
                    <tr key={o.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 font-medium text-slate-900">
                        <div className="flex items-center gap-2">
                          <span className="max-w-[220px] truncate" title={o.establishment_name}>
                            {o.establishment_name}
                          </span>
                          <SourceBadge source={o.source} />
                          <PopulationBadge population={o.population} />
                        </div>
                      </td>
                      <td className="px-4 py-3 capitalize text-slate-600">{o.establishment_type}</td>
                      <td className="px-4 py-3 text-slate-600">{o.city}</td>
                      <td className="px-4 py-3"><SignalBadge label={o.main_signal} /></td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col items-start gap-1">
                          <LifecycleBadge label={o.lifecycle_label} />
                          <StageBadge stage={o.lifecycle_stage} />
                          <HeatBadge heat={o.heat} />
                        </div>
                      </td>
                      <td className="px-4 py-3"><ScoreBadge score={o.opportunity_score} /></td>
                      <td className="px-4 py-3 max-w-[180px] truncate text-slate-500" title={o.probable_needs.join(", ")}>
                        {o.probable_needs[0] ?? "—"}
                      </td>
                      <td className="px-4 py-3"><ContactIcons o={o} /></td>
                      <td className="px-4 py-3"><StatusBadge status={o.status} /></td>
                      <td className="px-4 py-3 whitespace-nowrap text-slate-500">{formatDate(o.detection_date)}</td>
                      <td className="px-4 py-3 text-right">
                        <Link
                          href={`/opportunities/${o.id}`}
                          className="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-200"
                        >
                          Voir
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
