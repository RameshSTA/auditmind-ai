/**
 * The single source of truth for "what pages exist and where do they link" — shared by Sidebar.tsx
 * and CommandPalette.tsx so the two navigation surfaces can't drift out of sync with each other.
 */
import {
  AlertTriangle,
  Bot,
  FileSearch,
  FileText,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  Network,
  Search,
  ShieldAlert,
  Sliders,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
  isActive: (pathname: string) => boolean;
};

export function engagementIdFromPathname(pathname: string): string | null {
  const match = /^\/engagements\/([^/]+)/.exec(pathname);
  return match?.[1] ?? null;
}

export const GLOBAL_NAV_ITEMS: readonly NavItem[] = [
  {
    key: "dashboard",
    label: "Dashboard",
    href: "/engagements",
    icon: LayoutDashboard,
    isActive: (p) => p === "/engagements",
  },
  {
    key: "monitoring",
    label: "Monitoring",
    href: "/monitoring",
    icon: Gauge,
    isActive: (p) => p === "/monitoring",
  },
];

export const SETTINGS_NAV_ITEM: NavItem = {
  key: "settings",
  label: "Settings",
  href: "/settings",
  icon: Sliders,
  isActive: (p) => p === "/settings",
};

/** AI Copilot's own nav entry, kept separate from `workspaceNavItems` so Sidebar.tsx can render it
 * with top-level prominence (right after Dashboard/Monitoring, before the collapsible Workspace
 * group) instead of buried in a 9-item list. Still engagement-scoped (chat is per-engagement, same
 * as everything else here), so it only appears once an engagement is selected. */
export function copilotNavItem(engagementId: string): NavItem {
  const href = `/engagements/${engagementId}/copilot`;
  return {
    key: "copilot",
    label: "AI Copilot",
    href,
    icon: Bot,
    isActive: (p) => p.startsWith(href),
  };
}

export function workspaceNavItems(engagementId: string): NavItem[] {
  const base = `/engagements/${engagementId}`;
  return [
    { key: "evidence", label: "Documents & Evidence", href: base, icon: Search, isActive: (p) => p === base },
    {
      key: "findings",
      label: "Findings",
      href: `${base}/findings`,
      icon: FileText,
      isActive: (p) => p.startsWith(`${base}/findings`),
    },
    {
      key: "risk",
      label: "Risk & Anomalies",
      href: `${base}/risk`,
      icon: ShieldAlert,
      isActive: (p) => p.startsWith(`${base}/risk`),
    },
    {
      key: "fraud-detection",
      label: "Fraud Detection",
      href: `${base}/fraud-detection`,
      icon: AlertTriangle,
      isActive: (p) => p.startsWith(`${base}/fraud-detection`),
    },
    {
      key: "investigations",
      label: "Investigations",
      href: `${base}/investigations`,
      icon: FileSearch,
      isActive: (p) => p.startsWith(`${base}/investigations`),
    },
    {
      key: "knowledge-graph",
      label: "Knowledge Graph",
      href: `${base}/knowledge-graph`,
      icon: Network,
      isActive: (p) => p.startsWith(`${base}/knowledge-graph`),
    },
    {
      key: "reports",
      label: "Reports",
      href: `${base}/reports`,
      icon: FileText,
      isActive: (p) => p.startsWith(`${base}/reports`),
    },
    {
      key: "evaluation",
      label: "Evaluation",
      href: `${base}/evaluation`,
      icon: FlaskConical,
      isActive: (p) => p.startsWith(`${base}/evaluation`),
    },
    {
      key: "administration",
      label: "Administration",
      href: `${base}/administration`,
      icon: Users,
      isActive: (p) => p.startsWith(`${base}/administration`),
    },
  ];
}
