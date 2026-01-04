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
    <div className="border rounded p-4 bg-white shadow-sm">
      <div className="text-sm font-semibold mb-3">Match Score Trend</div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={formatted}>
            <XAxis dataKey="day" />
            <YAxis domain={[0, 100]} />
            <Tooltip />
            <Line type="monotone" dataKey="match_score" dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}