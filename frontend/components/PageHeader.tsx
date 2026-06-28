export default function PageHeader({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
      <div className="flex h-16 items-center justify-between px-8">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
          {subtitle && <p className="text-sm text-slate-500">{subtitle}</p>}
        </div>
        {children && <div className="flex items-center gap-2">{children}</div>}
      </div>
    </header>
  );
}
