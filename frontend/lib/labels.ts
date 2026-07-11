// Libellés et helpers de présentation partagés.

export const STATUS_LABELS: Record<string, string> = {
  non_contacte: "Non contacté",
  contacte: "Contacté",
  relance: "Relance",
  interesse: "Intéressé",
  rdv: "RDV",
  gagne: "Gagné",
  perdu: "Perdu",
};

export const STATUS_ORDER = [
  "non_contacte",
  "contacte",
  "relance",
  "interesse",
  "rdv",
  "gagne",
  "perdu",
];

// Classes Tailwind par statut (badge).
export const STATUS_STYLES: Record<string, string> = {
  non_contacte: "bg-slate-100 text-slate-600 ring-slate-200",
  contacte: "bg-blue-50 text-blue-700 ring-blue-200",
  relance: "bg-amber-50 text-amber-700 ring-amber-200",
  interesse: "bg-violet-50 text-violet-700 ring-violet-200",
  rdv: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  gagne: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  perdu: "bg-rose-50 text-rose-700 ring-rose-200",
};

export const CHANNEL_LABELS: Record<string, string> = {
  instagram: "Instagram",
  telephone: "Téléphone",
  email: "Email",
  linkedin: "LinkedIn",
};

export function scoreTier(score: number): {
  label: string;
  classes: string;
  dot: string;
} {
  // NB : "Élevé/Moyen/Faible" (et pas Chaud/…) pour ne pas entrer en collision
  // avec l'axe HEAT (chaud/tiède/froid), qui est une autre notion.
  if (score >= 8)
    return {
      label: "Élevé",
      classes: "bg-rose-50 text-rose-700 ring-rose-200",
      dot: "bg-rose-500",
    };
  if (score >= 5)
    return {
      label: "Moyen",
      classes: "bg-amber-50 text-amber-700 ring-amber-200",
      dot: "bg-amber-500",
    };
  return {
    label: "Faible",
    classes: "bg-sky-50 text-sky-700 ring-sky-200",
    dot: "bg-sky-500",
  };
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleDateString("fr-FR", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return value;
  }
}

// Format compact fr des abonnés Instagram : 335, 1,2k, 16k, 1,1M.
// "les petits comptes répondent plus souvent" -> affiché à côté du handle
// pour un repérage à vue d'œil dans la liste comme sur la fiche détail.
export function formatFollowers(n: number | null | undefined): string | null {
  if (n === null || n === undefined) return null;
  if (n < 1000) return `${n}`;
  if (n < 1_000_000) {
    const k = n / 1000;
    return `${k < 10 ? k.toFixed(1).replace(".", ",").replace(",0", "") : Math.round(k)}k`;
  }
  const m = n / 1_000_000;
  return `${m < 10 ? m.toFixed(1).replace(".", ",").replace(",0", "") : Math.round(m)}M`;
}

export const ACTION_LABELS: Record<string, string> = {
  message_genere: "Messages générés",
  statut_change: "Changement de statut",
  relance_planifiee: "Relance planifiée",
  note: "Note ajoutée",
  ingested: "Importé automatiquement",
};

export const SOURCE_LABELS: Record<string, string> = {
  demo: "Démo",
  bodacc: "BODACC",
  instagram: "Instagram",
  // Population architectes (A2).
  annuaire: "Annuaire",
  jeunes_studios: "Jeune studio",
  // Population architectes (B — volume max).
  sirene_stock: "Sirene (stock)",
  places: "Google Places",
};

// Libellés FR des labels d'éval (jeu de vérité + prédictions du pipeline).
export const EVAL_LABEL_LABELS: Record<string, string> = {
  opening: "Ouverture",
  opening_soon: "Ouverture prochaine",
  just_opened: "Ouvert récemment",
  renovation: "Rénovation en cours",
  established: "Établi",
  chain_multisite: "Chaîne multi-sites",
  not_venue: "Hors CHR",
  noise: "Bruit",
  unknown: "Indéterminé",
};

// Classes Tailwind par label d'éval (badge).
export const EVAL_LABEL_STYLES: Record<string, string> = {
  opening: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  opening_soon: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  just_opened: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  renovation: "bg-orange-50 text-orange-700 ring-orange-200",
  established: "bg-slate-100 text-slate-600 ring-slate-200",
  chain_multisite: "bg-violet-50 text-violet-700 ring-violet-200",
  not_venue: "bg-rose-50 text-rose-700 ring-rose-200",
  noise: "bg-amber-50 text-amber-700 ring-amber-200",
  unknown: "bg-slate-100 text-slate-500 ring-slate-200",
};

// Libellés FR du label de cycle de vie PERSISTÉ (funnel Insta, brique 3bis) —
// badge + filtre sur les fiches opportunités. NULL pour les sources registre
// (BODACC/Sirene) qui n'étiquettent pas encore.
export const LIFECYCLE_LABEL_LABELS: Record<string, string> = {
  opening_soon: "Ouverture prochaine",
  just_opened: "Vient d'ouvrir",
  renovation: "Rénovation en cours",
  established: "Établi",
  chain_multisite: "Chaîne / multi-sites",
  unknown: "À qualifier",
  // Population architectes (A1).
  studio_actif: "Studio actif",
  studio_dormant: "Studio en sommeil",
};

// Ordre d'affichage (filtre + légendes) — même ordre que LABEL_ORDER côté éval.
// Scindé par population : le dropdown cycle de vie s'adapte au filtre population
// (un combo CHR × studio_actif serait toujours vide).
export const LIFECYCLE_LABEL_ORDER_CHR = [
  "opening_soon",
  "just_opened",
  "renovation",
  "established",
  "chain_multisite",
  "unknown",
];
export const LIFECYCLE_LABEL_ORDER_ARCHI = ["studio_actif", "studio_dormant"];
export const LIFECYCLE_LABEL_ORDER = [
  ...LIFECYCLE_LABEL_ORDER_CHR,
  ...LIFECYCLE_LABEL_ORDER_ARCHI,
];

// Classes Tailwind par label de cycle de vie (badge) — mêmes teintes que
// EVAL_LABEL_STYLES pour rester cohérent avec les badges d'éval.
export const LIFECYCLE_LABEL_STYLES: Record<string, string> = {
  opening_soon: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  just_opened: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  renovation: "bg-orange-50 text-orange-700 ring-orange-200",
  established: "bg-slate-100 text-slate-600 ring-slate-200",
  chain_multisite: "bg-violet-50 text-violet-700 ring-violet-200",
  unknown: "bg-slate-100 text-slate-500 ring-slate-200",
  studio_actif: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  studio_dormant: "bg-slate-100 text-slate-500 ring-slate-200",
};

// Population du lead (A1) : CHR (défaut) ou architectes d'intérieur (prescripteurs).
export const POPULATION_LABELS: Record<string, string> = {
  chr: "CHR",
  architecte: "Architecte",
};

export const POPULATION_STYLES: Record<string, string> = {
  chr: "bg-slate-100 text-slate-500 ring-slate-200",
  architecte: "bg-indigo-50 text-indigo-700 ring-indigo-200",
};
