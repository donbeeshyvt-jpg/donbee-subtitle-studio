import { useId, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

// 版面整理用的可摺疊區塊：狀態記在 localStorage（每個瀏覽器各自記），內容保持掛載以免表單狀態遺失；
// lazy＝第一次展開才掛載（給會打 API 的重面板用）。
function readState(key: string, fallback: boolean): boolean {
  try {
    const saved = localStorage.getItem(key);
    return saved === null ? fallback : saved === "1";
  } catch {
    return fallback;
  }
}

export default function CollapsibleSection({
  id,
  title,
  hint,
  badge,
  defaultOpen = true,
  lazy = false,
  actions,
  className = "",
  children,
}: {
  id: string;
  title: string;
  hint?: ReactNode;
  badge?: ReactNode;
  defaultOpen?: boolean;
  lazy?: boolean;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const storageKey = `dongbi.section.${id}`;
  const [open, setOpen] = useState(() => readState(storageKey, defaultOpen));
  const [mounted, setMounted] = useState(open || !lazy);
  const bodyId = useId();
  const toggle = () =>
    setOpen((previous) => {
      const next = !previous;
      if (next) setMounted(true);
      try {
        localStorage.setItem(storageKey, next ? "1" : "0");
      } catch {
        /* 瀏覽器儲存不可用時只保留在記憶體 */
      }
      return next;
    });
  return (
    <section className={`collapsible ${open ? "is-open" : "is-closed"}`} data-section={id}>
      <div className="collapsible-head">
        <button type="button" className="collapsible-toggle" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
          {open ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
          <span className="collapsible-title">{title}</span>
          {badge !== undefined && badge !== null && badge !== "" && <span className="count">{badge}</span>}
          {hint && <span className="muted collapsible-hint">{hint}</span>}
        </button>
        {actions && <div className="collapsible-actions">{actions}</div>}
      </div>
      <div id={bodyId} className={`collapsible-body ${className}`.trim()} hidden={!open}>
        {mounted ? children : null}
      </div>
    </section>
  );
}
