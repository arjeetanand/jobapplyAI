import {
  BriefcaseBusiness,
  ClipboardCheck,
  History,
  ListChecks,
  Linkedin,
  ShieldCheck,
  Upload
} from "lucide-react";

export const sections = [
  { id: "resume", label: "Resume Intake", icon: Upload, idx: "01 / 07" },
  { id: "linkedin", label: "Find Jobs", icon: Linkedin, idx: "02 / 07" },
  { id: "review", label: "Match & Resume", icon: ShieldCheck, idx: "03 / 07" },
  { id: "questions", label: "Questions KB", icon: ListChecks, idx: "04 / 07" },
  { id: "tracker", label: "Applications", icon: BriefcaseBusiness, idx: "05 / 07" },
  { id: "logs", label: "Logs", icon: History, idx: "06 / 07" },
  { id: "browser", label: "Page Import", icon: ClipboardCheck, idx: "07 / 07" }
] as const;

export type SectionId = string;

export function Sidebar({
  active,
  setActive
}: {
  active: SectionId;
  setActive: (id: SectionId) => void;
}) {
  return (
    <aside className="border-r border-[#252525] bg-[#111111] text-[#f7f2e8]">
      <div className="border-b border-[#2e2e2e] px-5 py-6">
        <div className="section-kicker text-[#b7ff29]">Playbook</div>
        <div className="mt-2 text-2xl font-extrabold tracking-tight">SeekApply</div>
        <div className="mt-1 text-xs text-[#b8b1a4]">Resume-first application handbook</div>
      </div>
      <nav className="grid gap-2 p-3">
        {sections.map((item) => {
          const Icon = item.icon;
          const selected = active === item.id;
          return (
            <button
              key={item.id}
              className={`focus-ring floating-lift flex min-h-14 items-center gap-3 rounded-xl border px-3 text-left transition ${
                selected
                  ? "border-[#b7ff29] bg-[#1f2a12] text-[#f7f2e8]"
                  : "border-[#2b2b2b] bg-[#181818] text-[#d7cfbf] hover:bg-[#202020]"
              }`}
              onClick={() => setActive(item.id)}
              title={item.label}
            >
              <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${selected ? "bg-[#b7ff29] text-[#121212]" : "bg-[#252525] text-[#f7f2e8]"}`}>
                <Icon size={15} />
              </div>
              <div className="min-w-0">
                <div className="section-kicker text-[10px] text-[#9f978a]">{item.idx}</div>
                <div className="truncate text-sm font-semibold">{item.label}</div>
              </div>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
