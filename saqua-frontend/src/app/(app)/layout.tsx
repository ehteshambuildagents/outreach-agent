import { Sidebar } from "@/components/shell/sidebar";
import { Topbar } from "@/components/shell/topbar";
import { ApiAuthBridge } from "@/components/auth/api-auth-bridge";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      {/* Ambient depth behind the glass surfaces — muted indigo + teal blooms. */}
      <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="bloom-indigo absolute -left-48 top-[-12%] size-[620px] rounded-full" />
        <div className="bloom-teal absolute right-[-16%] top-[28%] size-[560px] rounded-full" />
        <div className="bloom-indigo absolute bottom-[-18%] left-[38%] size-[520px] rounded-full opacity-70" />
      </div>
      <ApiAuthBridge />
      <Sidebar />
      <div className="lg:pl-[var(--rail-w)]">
        <Topbar />
        <main className="mx-auto max-w-[1240px] px-5 pb-28 pt-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}
