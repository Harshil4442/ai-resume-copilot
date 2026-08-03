export default function Template({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full w-full flex-grow flex-col">
      {children}
    </div>
  );
}
