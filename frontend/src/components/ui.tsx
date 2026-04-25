import { ReactNode } from "react";

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-[18px] border border-line bg-[#fffdf7] shadow-panel floating-lift ${className}`}>{children}</section>;
}

export function Button({
  children,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" }) {
  const variants = {
    primary: "border border-[#9fdc15] bg-[#b7ff29] text-[#121212] hover:bg-[#cbff57]",
    secondary: "border border-line bg-[#fffdf7] text-ink hover:bg-[#f2ead9]",
    ghost: "text-ink hover:bg-[#efe6d5]"
  };
  return (
    <button
      className={`focus-ring floating-lift inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition ${variants[variant]} disabled:cursor-not-allowed disabled:opacity-50`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  children
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-2 text-sm font-medium text-ink">
      <span className="section-kicker">{label}</span>
      {children}
    </label>
  );
}

export const inputClass =
  "focus-ring h-11 w-full rounded-xl border border-line bg-[#fffdf7] px-3 text-sm text-ink shadow-sm placeholder:text-slate-400";

export const textareaClass =
  "focus-ring min-h-24 w-full rounded-xl border border-line bg-[#fffdf7] px-3 py-2 text-sm text-ink shadow-sm placeholder:text-slate-400";
