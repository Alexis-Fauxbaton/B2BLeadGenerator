"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";

export default function CopyButton({
  text,
  label,
  disabled,
}: {
  text: string | null | undefined;
  label: string;
  disabled?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  return (
    <button
      onClick={copy}
      disabled={disabled || !text}
      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {copied ? <Check size={14} className="text-emerald-600" /> : <Copy size={14} />}
      {copied ? "Copié !" : label}
    </button>
  );
}
