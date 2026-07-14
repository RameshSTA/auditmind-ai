"use client";

/**
 * Cmd/Ctrl+K command palette — jump to any workspace page without touching the sidebar, the
 * keyboard-first affordance every reference product in the design brief (Linear, Raycast, Vercel)
 * leads with. Reuses Sidebar.tsx's own nav item list (`@/lib/nav`) so the two can't drift apart.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Search } from "lucide-react";

import {
  copilotNavItem,
  engagementIdFromPathname,
  GLOBAL_NAV_ITEMS,
  SETTINGS_NAV_ITEM,
  workspaceNavItems,
} from "@/lib/nav";

export function CommandPalette() {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const engagementId = engagementIdFromPathname(pathname);
  const items = useMemo(() => {
    const workspace = engagementId
      ? [copilotNavItem(engagementId), ...workspaceNavItems(engagementId)]
      : [];
    return [...GLOBAL_NAV_ITEMS, ...workspace, SETTINGS_NAV_ITEM];
  }, [engagementId]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => item.label.toLowerCase().includes(q));
  }, [items, query]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
    const frame = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  function go(item: (typeof items)[number]) {
    setOpen(false);
    router.push(item.href);
  }

  return (
    <>
      <button type="button" className="command-trigger" onClick={() => setOpen(true)}>
        <Search size={14} strokeWidth={1.75} aria-hidden="true" />
        <span>Jump to…</span>
        <span className="kbd">⌘K</span>
      </button>

      {open ? (
        <div className="dialog-overlay" onClick={() => setOpen(false)}>
          <div
            className="command-palette fade-in"
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              ref={inputRef}
              className="command-input"
              placeholder="Jump to a page…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActiveIndex((i) => Math.max(i - 1, 0));
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  const item = filtered[activeIndex];
                  if (item) go(item);
                } else if (e.key === "Escape") {
                  setOpen(false);
                }
              }}
              aria-label="Search pages"
              aria-activedescendant={filtered[activeIndex] ? `command-item-${filtered[activeIndex].key}` : undefined}
              role="combobox"
              aria-expanded
              aria-controls="command-list"
            />
            <div className="command-list" id="command-list" role="listbox">
              {filtered.length === 0 ? <div className="muted px-3 py-3">No matching page.</div> : null}
              {filtered.map((item, index) => {
                const Icon = item.icon;
                return (
                  <button
                    type="button"
                    id={`command-item-${item.key}`}
                    key={item.key}
                    role="option"
                    aria-selected={index === activeIndex}
                    className={`command-item${index === activeIndex ? " command-item-active" : ""}`}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => go(item)}
                  >
                    <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
                    <span>{item.label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
