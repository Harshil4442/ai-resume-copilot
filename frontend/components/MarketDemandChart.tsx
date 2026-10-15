"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function MarketDemandChart({ data }: { data: { skill: string; demand: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ left: 10, right: 12 }}>
        <CartesianGrid horizontal={false} stroke="var(--color-border)" />
        <XAxis type="number" domain={[0, 100]} tickFormatter={(value) => `${value}%`} tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} axisLine={false} />
        <YAxis type="category" dataKey="skill" width={100} tick={{ fill: "var(--color-foreground)", fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip formatter={(value) => [`${Number(value).toFixed(1)}%`, "Demand"]} contentStyle={{ background: "var(--color-card)", color: "var(--color-foreground)", border: "1px solid var(--color-border)", borderRadius: 12 }} />
        <Bar dataKey="demand" fill="var(--color-primary)" radius={[0, 4, 4, 0]} barSize={22} />
      </BarChart>
    </ResponsiveContainer>
  );
}
