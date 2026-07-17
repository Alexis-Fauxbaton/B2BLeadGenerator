// Numéro alternatif « à tester » (le principal reste `phone`) — cf.
// docs/plans/2026-07-17-multi-numeros-design.md. Jamais un doublon du
// principal, cap 5, alimenté par les producteurs et réordonné par la
// promotion manuelle (POST /phones/promote).
export interface PhoneCandidate {
  number: string;
  source: "site" | "annuaire" | "places" | "cross_fill" | "ex_principal";
  proof_url?: string | null;
  first_seen: string;
}

export interface OpportunityList {
  id: number;
  establishment_name: string;
  establishment_type: string;
  city: string;
  address: string;
  main_signal: string;
  secondary_signals: string[];
  detection_date: string;
  activity_start_date: string | null;
  venue_origin_date: string | null;
  estimated_timing: string;
  probable_needs: string[];
  decision_maker: string | null;
  dirigeants: string[];
  opportunity_score: number;
  score_reason: string;
  recommended_channel: string;
  channel_reason: string;
  proof_text: string;
  proof_url: string;
  status: string;
  source: string;
  source_ref: string | null;
  lifecycle_label: string | null;
  population: string;
  siren: string | null;
  naf: string | null;
  phone: string | null;
  phone_candidates: PhoneCandidate[];
  email: string | null;
  extra_emails: string[];
  website: string | null;
  instagram: string | null;
  followers_count: number | null;
  facebook: string | null;
  latitude: number | null;
  longitude: number | null;
  review_count: number | null;
  lifecycle_stage: string;
  heat: string;
  freshness: string;
  contact_confidence: string | null;
  decision_maker_email: string | null;
  decision_maker_confidence: string | null;
  next_follow_up_date: string | null;
  next_action: string | null;
  assigned_to: string | null;
  created_at: string;
  updated_at: string;
}

// Compte (auth légère) : le patron (admin) et ses closers.
export interface User {
  id: number;
  name: string;
  email: string;
  role: "admin" | "closer";
  created_at: string;
}

// Version allégée d'un compte (GET /api/auth/users, dropdown "Assigné à") :
// id + nom SEULEMENT — jamais email/role (énumération de comptes).
export interface UserPublic {
  id: number;
  name: string;
}

export interface IngestStats {
  source: string;
  fetched: number;
  chr_matched: number;
  created: number;
  updated: number;
  skipped_dupes: number;
  errors: number;
}

export interface SignalRead {
  id: number;
  signal_type: string;
  source: string;
  source_url: string;
  signal_date: string;
  confidence_score: number;
  raw_text: string;
}

export interface ContactHistoryRead {
  id: number;
  channel: string | null;
  message: string | null;
  action_type: string;
  status: string | null;
  note: string | null;
  contacted_at: string | null;
  created_at: string;
}

// Journal d'activités (suivi de contact) : geste rapide sur une fiche —
// distinct de ContactHistoryRead (événements système : message généré, import).
export interface ContactActivity {
  id: number;
  opportunity_id: number;
  type: "appel" | "email" | "dm_insta" | "note" | "statut";
  note: string | null;
  // Auteur (closer) — fondation des comptes closers. Exposé en lecture ; pas
  // encore renseigné ni affiché (l'auth viendra plus tard).
  author: string | null;
  // Qualification cross-canal (N1/N2/N3) — lecture seule ici, jamais réécrite
  // sur la fiche. `issue` = NULL pour une action d'émission (ex. « Email
  // envoyé ») sans résultat encore connu.
  issue: "joint" | "pas_joint" | "ko" | null;
  raison: string | null;
  detail: string[];
  // Contact EFFECTIVEMENT tenté au moment du geste (numéro/email/handle) —
  // cf. docs/plans/2026-07-17-multi-numeros-design.md §3. Lecture seule ici.
  contact_used: string | null;
  created_at: string;
}

// Taxonomie de qualification servie par GET /api/meta (`qualif_taxonomy`) —
// source de vérité pour la VALIDITÉ des combinaisons (backend = autorité).
// `raisons` : { canal: { issue: [raisons] } }. Les libellés FR restent dans
// lib/labels.ts, comme pour le reste de l'app.
export interface QualifTaxonomy {
  issues: string[];
  raisons: Record<string, Record<string, string[]>>;
  details: string[];
}

// --- Monitoring des résultats de qualification (GET /api/activite/stats) ----
// 100% lecture agrégée, aucun effet de bord sur les fiches (cf. design
// qualification §2).

export interface QualifKpis {
  tentatives: number;
  joignabilite: number | null;
  volume_appels: number;
  reponses_email_dm: number;
}

export interface QualifCloserStats {
  closer: string | null;
  tentatives: number;
  joints: number;
  joignabilite: number | null;
}

export interface QualifChannelStats {
  type: string;
  tentatives: number;
  joints: number;
  joignabilite: number | null;
}

export interface QualifKoReason {
  raison: string;
  count: number;
}

export interface QualifDailyVolume {
  day: string;
  count: number;
}

export interface QualifStats {
  period_start: string;
  period_end: string;
  kpis: QualifKpis;
  by_closer: QualifCloserStats[];
  by_channel: QualifChannelStats[];
  top_ko_reasons: QualifKoReason[];
  daily_call_volume: QualifDailyVolume[];
}

// Dernière issue connue d'une fiche (GET /api/opportunities/last-issues,
// batch) — DÉRIVÉE à la volée pour l'affichage (puce « dernier contact »),
// jamais persistée sur la fiche.
export interface LastIssue {
  opportunity_id: number;
  issue: string;
  raison: string | null;
  at: string;
}

// Vue « À relancer », groupée par échéance (voir GET /api/followups).
export interface FollowUpBuckets {
  en_retard: OpportunityList[];
  aujourdhui: OpportunityList[];
  cette_semaine: OpportunityList[];
}

// Compteur léger pour le badge de nav (GET /api/followups/count).
export interface FollowUpCount {
  en_retard: number;
  aujourdhui: number;
  cette_semaine: number;
  total: number;
}

export interface OpportunityRead extends OpportunityList {
  generated_instagram_dm: string | null;
  generated_email: string | null;
  generated_linkedin: string | null;
  generated_call_script: string | null;
  signals: SignalRead[];
  contact_history: ContactHistoryRead[];
}

export interface GeneratedMessages {
  instagram_dm: string;
  email: string;
  linkedin: string;
  call_script: string;
  source: string;
}

export interface DashboardStats {
  total_opportunities: number;
  hot_leads: number;
  not_contacted: number;
  follow_ups_due: number;
  interested: number;
  appointments: number;
  won: number;
  lost: number;
  by_signal: { label: string; count: number }[];
  by_status: { label: string; count: number }[];
  hottest: OpportunityList[];
}

export interface Settings {
  id: number;
  provider_name: string;
  provider_offer: string;
  tone: string;
  target_area: string;
  updated_at: string;
}

export interface Meta {
  establishment_types: string[];
  signal_types: string[];
  channels: string[];
  statuses: string[];
  cities: string[];
  activity_types: string[];
  qualif_taxonomy: QualifTaxonomy;
}

export type Pipeline = Record<string, OpportunityList[]>;

export interface GroundtruthRow {
  handle: string;
  name: string;
  label: string;
  confidence: string;
  rationale: string;
  annotated_at: string;
  ig_url: string;
  has_snapshot: boolean;
  predicted: string | null;
  disagreement: boolean;
}

export interface GroundtruthResult {
  as_of: string | null;
  total: number;
  rows: GroundtruthRow[];
}

// Vue patron /activite : journal global des activités + compteurs par closer.
export interface ActivityJournalEntry {
  id: number;
  opportunity_id: number;
  opportunity_name: string | null;
  type: string;
  note: string | null;
  author: string | null;
  // Contact effectivement tenté au moment du geste — sans lui, deux lignes
  // « Appel · Mauvais numéro » sont indiscernables (revue produit 2026-07-17).
  contact_used: string | null;
  created_at: string;
}

export interface AuthorCount {
  author: string | null;
  count: number;
}

export interface ActivityJournal {
  day: string;
  activities: ActivityJournalEntry[];
  counts: AuthorCount[];
}
