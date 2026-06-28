import type { LucideIcon } from "lucide-react";

export default function StatCard({
  label,
  value,
  icon: Icon,
  accent = "slate",
  hint,
}: {
  label: string;
  value: number | string;
  icon: LucideIcon;
  accent?: "slate" | "rose" | "amber" | "violet" | "cyan" | "emerald" | "brand";
  hint?: string;
}) {
  const accents: Record<string, string> = {
    slate: "bg-slate-100 text-slate-600",
    rose: "bg-rose-100 text-rose-600",
    amber: "bg-amber-100 text-amber-600",
    violet: "bg-violet-100 text-violet-600",
    cyan: "bg-cyan-100 text-cyan-600",
    emerald: "bg-emerald-100 text-emerald-600",
    brand: "bg-brand-100 text-brand-600",
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-slate-500">{label}</p>
          <p className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
            {value}
          </p>
          {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
        </div>
        <div className={`grid h-10 w-10 place-items-center rounded-lg ${accents[accent]}`}>
          <Icon size={20} />
        </div>
      </div>
    </div>
  );
}
