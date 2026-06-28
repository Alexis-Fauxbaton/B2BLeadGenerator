import { Loader2, AlertTriangle, Inbox } from "lucide-react";

export function Loading({ label = "Chargement…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-slate-400">
      <Loader2 className="animate-spin" size={18} />
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="mx-8 my-10 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700">
      <AlertTriangle size={18} className="mt-0.5 shrink-0" />
      <div>
        <p className="font-medium">Impossible de joindre l'API</p>
        <p className="mt-1 text-rose-600">{message}</p>
        <p className="mt-2 text-rose-500">
          Vérifie que le backend tourne sur{" "}
          <code className="rounded bg-rose-100 px-1">http://localhost:8000</code>.
        </p>
      </div>
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-slate-400">
      <Inbox size={28} />
      <span className="text-sm">{label}</span>
    </div>
  );
}
