import {
  LayoutDashboard,
  Sparkles,
  Users,
  Megaphone,
  Workflow,
  Inbox,
  Plug,
  BarChart3,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  badge?: string;
  /** Shown in the compact mobile tab bar (space is limited to a handful). */
  primary?: boolean;
}

export interface NavGroup {
  title?: string;
  items: NavItem[];
}

/**
 * Information architecture — one flat, ordered rail that mirrors the product's
 * real capabilities (every href resolves to an existing route). `primary` marks
 * the handful surfaced in the mobile tab bar so it never overflows.
 */
export const NAV: NavGroup[] = [
  {
    items: [
      { label: "Home", href: "/dashboard", icon: LayoutDashboard, primary: true },
      { label: "Saqua AI", href: "/ai", icon: Sparkles, primary: true },
      { label: "Prospects", href: "/prospects", icon: Users, primary: true },
      { label: "Campaigns", href: "/campaigns", icon: Megaphone, primary: true },
      { label: "Automations", href: "/automation", icon: Workflow },
      { label: "Inbox", href: "/inbox", icon: Inbox, primary: true },
      { label: "Connections", href: "/connections", icon: Plug },
      { label: "Analytics", href: "/analytics", icon: BarChart3 },
      { label: "Settings", href: "/settings", icon: Settings },
    ],
  },
];
