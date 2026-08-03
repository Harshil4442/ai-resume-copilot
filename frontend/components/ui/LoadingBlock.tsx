export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-label="Loading" role="status">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="skeleton-block h-16 w-full" />
      ))}
    </div>
  );
}
