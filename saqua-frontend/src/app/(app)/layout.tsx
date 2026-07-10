import { Sidebar } from "@/components/shell/sidebar";
import { Topbar } from "@/components/shell/topbar";
import { ApiAuthBridge } from "@/components/auth/api-auth-bridge";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <ApiAuthBridge />
      <Sidebar />
      <div className="lg:pl-[var(--rail-w)]">
        <Topbar />
        <main className="mx-auto max-w-[1240px] px-5 pb-28 pt-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}
