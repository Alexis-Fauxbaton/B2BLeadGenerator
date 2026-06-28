"use client";

import { useEffect, useState } from "react";
import { Save, Check, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Settings } from "@/lib/types";
import PageHeader from "@/components/PageHeader";
import { Loading, ErrorState } from "@/components/States";

const FIELD_CLS =
  "w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getSettings().then(setSettings).catch((e) => setError(e.message));
  }, []);

  const update = (patch: Partial<Settings>) =>
    setSettings((s) => (s ? { ...s, ...patch } : s));

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setSaved(false);
    try {
      const updated = await api.updateSettings({
        provider_name: settings.provider_name,
        provider_offer: settings.provider_offer,
        tone: settings.tone,
        target_area: settings.target_area,
      });
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (error) return <ErrorState message={error} />;
  if (!settings)
    return (
      <>
        <PageHeader title="Settings" />
        <Loading />
      </>
    );

  return (
    <>
      <PageHeader
        title="Settings"
        subtitle="Profil fournisseur — influence la génération des messages"
      />

      <div className="max-w-2xl p-8">
        <div className="space-y-5 rounded-xl border border-slate-200 bg-white p-6 shadow-card">
          <Field label="Nom du fournisseur">
            <input
              className={FIELD_CLS}
              value={settings.provider_name}
              onChange={(e) => update({ provider_name: e.target.value })}
            />
          </Field>

          <Field label="Description de l'offre">
            <textarea
              rows={3}
              className={FIELD_CLS}
              value={settings.provider_offer}
              onChange={(e) => update({ provider_offer: e.target.value })}
            />
          </Field>

          <Field label="Ton des messages">
            <input
              className={FIELD_CLS}
              value={settings.tone}
              onChange={(e) => update({ tone: e.target.value })}
            />
          </Field>

          <Field label="Zone ciblée">
            <input
              className={FIELD_CLS}
              value={settings.target_area}
              onChange={(e) => update({ target_area: e.target.value })}
            />
          </Field>

          <div className="flex items-center gap-3 border-t border-slate-100 pt-4">
            <button
              onClick={save}
              disabled={saving}
              className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
            >
              {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
              Enregistrer
            </button>
            {saved && (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-600">
                <Check size={16} /> Enregistré en base
              </span>
            )}
          </div>
        </div>

        <p className="mt-4 text-xs text-slate-400">
          Ces réglages sont utilisés pour personnaliser les messages générés (OpenAI ou templates).
        </p>
      </div>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1.5 block text-sm font-medium text-slate-700">{label}</label>
      {children}
    </div>
  );
}
