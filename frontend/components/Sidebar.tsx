"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Target,
  BellRing,
  KanbanSquare,
  Settings as SettingsIcon,
  FlaskConical,
  Radar,
} from "lucide-react";
import { api } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/opportunities", label: "Opportunités", icon: Target },
  { href: "/followups", label: "À relancer", icon: BellRing },
  { href: "/pipeline", label: "Pipeline", icon: KanbanSquare },
  { href: "/eval", label: "Éval Instagram", icon: FlaskConical },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function Sidebar() {
  const pathname = usePathname();
  // Badge discret : "à faire maintenant" = en retard + aujourd'hui (voir
  // contrat GET /api/followups/count). Absent (pas de 0 affiché) tant que
  // rien n'est dû -> jamais criard.
  const [dueCount, setDueCount] = useState(0);

  useEffect(() => {
    api
      .getFollowUpsCount()
      .then((c) => setDueCount(c.en_retard + c.aujourdhui))
      .catch(() => {});
  }, []);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <aside className="w-64 shrink-0 border-r border-slate-200 bg-white flex flex-col">
      <div className="h-16 flex items-center gap-2.5 px-5 border-b border-slate-200">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 text-white">
          <Radar size={20} />
        </div>
        <div className="leading-tight">
          <div className="font-semibold text-slate-900">CHR Signal</div>
          <div className="text-xs text-slate-400">Radar — PoC</div>
        </div>
      </div>

      <nav className="flex-1 p-3 space-y-1">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = isActive(href);
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
                active
                  ? "bg-brand-50 text-brand-700"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
              }`}
            >
              <Icon size={18} className={active ? "text-brand-600" : "text-slate-400"} />
              <span className="flex-1">{label}</span>
              {href === "/followups" && dueCount > 0 && (
                <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-slate-600">
                  {dueCount}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-slate-200">
        <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">
          <p className="font-medium text-slate-700">Sources</p>
          <p className="mt-1">BODACC · Sirene · Instagram</p>
        </div>
      </div>
    </aside>
  );
}
