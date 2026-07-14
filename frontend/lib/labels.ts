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

// Journal d'activités (suivi de contact, ContactActivity) — distinct de
// ACTION_LABELS (événements système ContactHistory).
export const ACTIVITY_TYPE_LABELS: Record<string, string> = {
  appel: "Appel",
  email: "Email envoyé",
  dm_insta: "DM envoyé",
  note: "Note",
  statut: "Changement de statut",
};

// Le backend sérialise les timestamps naïfs (sans suffixe de fuseau, ex.
// "2026-07-13T11:18:20.081942") qui sont en UTC. new Date() sur une chaîne
// sans 'Z'/offset l'interprète comme heure LOCALE -> décalage. On force
// l'interprétation UTC quand aucun fuseau n'est déjà présent.
function parseBackendTimestamp(value: string): Date {
  const hasTimezone = /Z$|[+-]\d{2}:?\d{2}$/.test(value);
  const hasTime = value.includes("T");
  return new Date(hasTime && !hasTimezone ? `${value}Z` : value);
}

// Date relative fr, courte, pour le journal d'activités (toujours dans le
// passé). Retombe sur formatDate au-delà d'un an pour rester lisible.
export function formatRelativeDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = parseBackendTimestamp(value);
  if (Number.isNaN(d.getTime())) return value;
  const diffDays = Math.round((Date.now() - d.getTime()) / 86_400_000);
  if (diffDays <= 0) return "aujourd'hui";
  if (diffDays === 1) return "hier";
  if (diffDays < 30) return `il y a ${diffDays} j`;
  if (diffDays < 365) return `il y a ${Math.round(diffDays / 30)} mois`;
  return formatDate(value);
}

// Échéance relative fr pour la vue « À relancer » (dates calendaires, sans
// heure). "aujourd'hui" / "demain" / "dans N j" / "en retard de N j".
export function formatDueLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const due = new Date(`${value}T00:00:00`);
  if (Number.isNaN(due.getTime())) return value;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((due.getTime() - today.getTime()) / 86_400_000);
  if (diffDays === 0) return "aujourd'hui";
  if (diffDays === 1) return "demain";
  if (diffDays > 1) return `dans ${diffDays} j`;
  if (diffDays === -1) return "en retard de 1 j";
  return `en retard de ${Math.abs(diffDays)} j`;
}

// Une échéance passée (hors gagné/perdu, filtré côté appelant) = en retard.
// Comparaison sur la date LOCALE (comme formatDueLabel), pas UTC : entre
// 00:00 et 02:00 heure française l'été, toISOString() donne encore la
// veille et ferait manquer une relance due la veille.
export function isOverdue(value: string | null | undefined): boolean {
  if (!value) return false;
  const due = new Date(`${value}T00:00:00`);
  if (Number.isNaN(due.getTime())) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return due.getTime() < today.getTime();
}

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

// --- Qualification des contacts (issue/raison/detail, cross-canal) ----------
// Miroir FR de QUALIF_ISSUES/QUALIF_RAISONS/QUALIF_DETAILS (models.py) : le
// backend fait autorité sur la VALIDITÉ des combinaisons (servie en lecture
// via GET /api/meta -> qualif_taxonomy) ; les libellés FR restent ici, comme
// pour le reste de l'app. Cf. docs/plans/2026-07-14-qualification-contacts-design.md.

export const QUALIF_ISSUE_LABELS: Record<string, string> = {
  joint: "Joint",
  pas_joint: "Pas joint",
  ko: "KO",
};

// Couleur par N1 (badge) : vert = joint, ambre = pas joint, rouge = KO —
// cohérent dans toute l'app (barre de qualification, journal, listes).
export const QUALIF_ISSUE_STYLES: Record<string, string> = {
  joint: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  pas_joint: "bg-amber-50 text-amber-700 ring-amber-200",
  ko: "bg-rose-50 text-rose-700 ring-rose-200",
};

// Variante bouton (barre de qualification) — mêmes teintes que STATUS_STYLES.
export const QUALIF_ISSUE_BUTTON_STYLES: Record<string, string> = {
  joint: "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100",
  pas_joint: "border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100",
  ko: "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100",
};

export const QUALIF_ISSUE_DOT: Record<string, string> = {
  joint: "bg-emerald-500",
  pas_joint: "bg-amber-500",
  ko: "bg-rose-500",
};

// N2 — raisons, libellés à plat : les clés sont partagées entre canaux avec le
// même sens FR (ex. "interesse" -> "Intéressé" en appel/email/dm_insta).
export const QUALIF_RAISON_LABELS: Record<string, string> = {
  interesse: "Intéressé",
  a_rappeler: "À rappeler",
  pas_interesse: "Pas intéressé",
  repondeur: "Répondeur",
  pas_de_reponse: "Pas de réponse",
  occupe: "Occupé",
  mauvais_numero: "Mauvais numéro",
  ferme: "N'existe plus",
  ne_plus_contacter: "Ne pas recontacter",
  a_suivre: "À suivre",
  bounce: "Adresse invalide",
  desinscription: "Désinscription",
  vu_sans_reponse: "Vu, sans réponse",
  compte_introuvable: "Compte introuvable",
  bloque: "Bloqué",
};

// N3 — détails (chips optionnelles, réutilisables sous n'importe quel canal/issue).
export const QUALIF_DETAIL_LABELS: Record<string, string> = {
  deja_fournisseur: "A déjà un fournisseur",
  pas_de_projet: "Pas de projet",
  budget: "Budget",
  mauvais_interlocuteur: "Mauvais interlocuteur",
  rappeler_plus_tard: "Rappeler plus tard",
};

// Canal court (toggle de la barre de qualification) — distinct d'ACTIVITY_TYPE_LABELS
// qui porte le libellé de l'ACTION ("Email envoyé") plutôt que du canal seul.
export const QUALIF_CHANNEL_LABELS: Record<string, string> = {
  appel: "Appel",
  email: "Email",
  dm_insta: "DM",
};

// Puce « dernière issue » (listes /followups, journal) : le libellé de la
// raison si connue (plus précis, ex. "Répondeur"), sinon le N1 seul ("Joint").
export function formatIssueChip(issue: string, raison?: string | null): string {
  if (raison && QUALIF_RAISON_LABELS[raison]) return QUALIF_RAISON_LABELS[raison];
  return QUALIF_ISSUE_LABELS[issue] ?? issue;
}

// Canal recommandé (recommended_channel, ex. "telephone"/"email"/"instagram")
// -> canal de la barre de qualification (ACTIVITY_TYPES). Défaut "appel" pour
// les canaux sans équivalent direct (ex. "linkedin").
export function recommendedToActivityType(
  channel: string | null | undefined
): "appel" | "email" | "dm_insta" {
  if (channel === "email") return "email";
  if (channel === "instagram") return "dm_insta";
  return "appel";
}

// Population du lead (A1) : CHR (défaut) ou architectes d'intérieur (prescripteurs).
export const POPULATION_LABELS: Record<string, string> = {
  chr: "CHR",
  architecte: "Architecte",
};

export const POPULATION_STYLES: Record<string, string> = {
  chr: "bg-slate-100 text-slate-500 ring-slate-200",
  architecte: "bg-indigo-50 text-indigo-700 ring-indigo-200",
};
