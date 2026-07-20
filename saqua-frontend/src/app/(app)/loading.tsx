import { Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function AppLoading() {
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="space-y-2">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-4 w-80 max-w-full" />
        </div>
        <Skeleton className="h-9 w-36" />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <Card key={item} className="p-4">
            <Skeleton className="mb-5 size-8" />
            <Skeleton className="mb-2 h-4 w-20" />
            <Skeleton className="h-7 w-16" />
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[340px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Preparing workspace</CardTitle>
            <Sparkles className="size-4 animate-pulse-soft text-accent" />
          </CardHeader>
          <CardContent className="space-y-3">
            {["Reading campaign state...", "Loading prospects...", "Preparing AI steps..."].map((line) => (
              <div key={line} className="flex items-center gap-3 rounded-md border border-border-faint bg-black/[0.02] p-3">
                <Skeleton className="size-6 rounded-full" />
                <div className="flex-1 text-xs text-muted">{line}</div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-5 w-16" />
          </CardHeader>
          <CardContent className="space-y-3">
            {[0, 1, 2, 3, 4].map((row) => (
              <div key={row} className="grid gap-3 rounded-md border border-border-faint bg-black/[0.02] p-3 md:grid-cols-[1fr_100px_90px]">
                <Skeleton className="h-5" />
                <Skeleton className="h-5" />
                <Skeleton className="h-5" />
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
