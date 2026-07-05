"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Target,
  KanbanSquare,
  Settings as SettingsIcon,
  FlaskConical,
  Radar,
} from "lucide-react";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/opportunities", label: "Opportunités", icon: Target },
  { href: "/pipeline", label: "Pipeline", icon: KanbanSquare },
  { href: "/eval", label: "Éval Instagram", icon: FlaskConical },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function Sidebar() {
  const pathname = usePathname();

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
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="p-4 border-t border-slate-200">
        <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">
          <p className="font-medium text-slate-700">Mode démo</p>
          <p className="mt-1">Données seedées · aucune source réelle connectée.</p>
        </div>
      </div>
    </aside>
  );
}
