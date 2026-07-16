import {
  LayoutDashboard,
  Megaphone,
  MessageSquare,
  PlusCircle,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  badge?: string;
}

export interface NavGroup {
  title?: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    items: [
      { label: "Chat", href: "/ai", icon: MessageSquare },
      { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
      { label: "New Campaign", href: "/campaigns/new", icon: PlusCircle },
      { label: "Prospects", href: "/prospects", icon: Users },
      { label: "Campaigns", href: "/campaigns", icon: Megaphone },
      { label: "Settings", href: "/settings", icon: Settings },
    ],
  },
];
