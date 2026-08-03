export default function AnimatedBackground() {
  return (
    <div className="fixed inset-0 z-[-1] overflow-hidden bg-background" aria-hidden="true">
      <div className="hirewiz-grid absolute inset-0" />
    </div>
  );
}
