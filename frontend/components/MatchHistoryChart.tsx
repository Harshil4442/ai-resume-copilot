import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

type AnyHistoryItem = {
  timestamp?: string;
  created_at?: string;
  day?: string;
  match_score?: number;
  score?: number;
};

export default function MatchHistoryChart({ data }: { data: AnyHistoryItem[] }) {
  const formatted = (data || [])
    .map((d) => {
      const scoreRaw = d.match_score ?? d.score;
      const score = typeof scoreRaw === "number" && Number.isFinite(scoreRaw) ? scoreRaw : 0;

      const dateStr = d.timestamp ?? d.created_at;
      const day = d.day || (dateStr ? new Date(dateStr).toLocaleDateString() : "");

      return { day, match_score: score };
    })
    .filter((d) => d.day);

  return (
    <div className="panel kinetic-border p-5">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <div className="label-kicker">Trajectory</div>
          <div className="text-xl font-black text-slate-950 mt-1">Match Score Trend</div>
        </div>
        <span className="signal-chip">Live history</span>
      </div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={formatted}>
            <XAxis dataKey="day" />
            <YAxis domain={[0, 100]} />
            <Tooltip />
            <Line type="monotone" dataKey="match_score" stroke="#2563eb" strokeWidth={4} dot={{ r: 4 }} activeDot={{ r: 7 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
