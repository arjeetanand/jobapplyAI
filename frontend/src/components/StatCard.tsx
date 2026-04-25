import { ReactNode } from "react";

export function StatCard({ label, value, icon }: { label: string; value: string | number; icon: ReactNode }) {
  return (
    <div className="floating-lift rounded-[18px] border border-line bg-[#fffdf7] p-4 shadow-panel">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="section-kicker">{label}</div>
          <div className="mt-2 text-3xl font-black tracking-tight text-ink">{value}</div>
        </div>
        <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-[#d4e998] bg-[#b7ff29] text-[#121212]">{icon}</div>
      </div>
    </div>
  );
}
