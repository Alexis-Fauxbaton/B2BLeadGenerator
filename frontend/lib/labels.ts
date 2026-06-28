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
  if (score >= 8)
    return {
      label: "Chaud",
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
    label: "Froid",
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
};
