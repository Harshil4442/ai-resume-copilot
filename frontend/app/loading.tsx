import { LoadingBlock } from "../components/ui/LoadingBlock";

export default function Loading() {
  return (
    <main className="app-page">
      <div className="page-container space-y-8">
        <p className="text-sm text-muted-foreground">Opening your workspace…</p>
        <LoadingBlock rows={4} />
      </div>
    </main>
  );
}
