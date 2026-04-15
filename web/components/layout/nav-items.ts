import {
  LayoutDashboard,
  Users,
  Repeat,
  RefreshCw,
  KeyRound,
  Activity,
  ScrollText,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  key: string;
  href: string;
  label: string;
  description: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  {
    key: "dashboard",
    href: "/dashboard",
    label: "仪表盘",
    description: "账号池状态总览",
    icon: LayoutDashboard,
  },
  {
    key: "team",
    href: "/team",
    label: "Team 成员",
    description: "ChatGPT Team 成员管理",
    icon: Users,
  },
  {
    key: "pool",
    href: "/pool",
    label: "账号池操作",
    description: "轮转 / 检查 / 添加 / 清理",
    icon: Repeat,
  },
  {
    key: "sync",
    href: "/sync",
    label: "同步中心",
    description: "与 CPA 同步 auth 文件",
    icon: RefreshCw,
  },
  {
    key: "oauth",
    href: "/oauth",
    label: "OAuth 登录",
    description: "手动导入账号授权",
    icon: KeyRound,
  },
  {
    key: "tasks",
    href: "/tasks",
    label: "任务历史",
    description: "后台任务状态与结果",
    icon: Activity,
  },
  {
    key: "logs",
    href: "/logs",
    label: "日志",
    description: "实时日志流",
    icon: ScrollText,
  },
  {
    key: "settings",
    href: "/settings",
    label: "设置",
    description: "主号登录 / 自动检查",
    icon: Settings,
  },
];
