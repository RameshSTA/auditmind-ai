"use client";

/**
 * Collapsible app sidebar. Route-aware: outside an engagement it shows Dashboard, Monitoring, and
 * Settings; inside one (`/engagements/[id]/...`) it adds the real per-engagement pages. Every
 * section in the nav is real — there is no Roadmap group left to render.
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronsLeft, ChevronsRight } from "lucide-react";

import {
  copilotNavItem,
  engagementIdFromPathname,
  GLOBAL_NAV_ITEMS,
  SETTINGS_NAV_ITEM,
  workspaceNavItems,
  type NavItem,
} from "@/lib/nav";

const COLLAPSE_KEY = "auditmind:sidebar-collapsed";

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem(COLLAPSE_KEY) === "1");
    setHydrated(true);
  }, []);

  function toggle() {
    const next = !collapsed;
    setCollapsed(next);
    window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
  }

  const engagementId = engagementIdFromPathname(pathname);
  const realItems = GLOBAL_NAV_ITEMS;
  const copilotItem: NavItem | null = engagementId ? copilotNavItem(engagementId) : null;
  const workspaceItems: NavItem[] = engagementId ? workspaceNavItems(engagementId) : [];

  return (
    <aside className={`sidebar${collapsed && hydrated ? " sidebar-collapsed" : ""}`}>
      <button
        type="button"
        onClick={toggle}
        className="sidebar-toggle"
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
      </button>

      <nav aria-label="Primary" className="sidebar-nav">
        {realItems.map((item) => (
          <SidebarLink key={item.key} item={item} active={item.isActive(pathname)} collapsed={collapsed} />
        ))}

        {copilotItem ? (
          <SidebarLink
            item={copilotItem}
            active={copilotItem.isActive(pathname)}
            collapsed={collapsed}
          />
        ) : null}

        {workspaceItems.length > 0 ? (
          <>
            <div className="sidebar-group-label">{collapsed ? "" : "Workspace"}</div>
            {workspaceItems.map((item) => (
              <SidebarLink key={item.key} item={item} active={item.isActive(pathname)} collapsed={collapsed} />
            ))}
          </>
        ) : null}

        <div className="flex-1" />
        <SidebarLink
          item={SETTINGS_NAV_ITEM}
          active={SETTINGS_NAV_ITEM.isActive(pathname)}
          collapsed={collapsed}
        />
      </nav>
    </aside>
  );
}

function SidebarLink({ item, active, collapsed }: { item: NavItem; active: boolean; collapsed: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={`nav-item${active ? " nav-item-active" : ""}`}
      aria-current={active ? "page" : undefined}
      title={collapsed ? item.label : undefined}
    >
      <Icon size={17} strokeWidth={1.75} aria-hidden="true" />
      {!collapsed ? <span>{item.label}</span> : null}
    </Link>
  );
}
