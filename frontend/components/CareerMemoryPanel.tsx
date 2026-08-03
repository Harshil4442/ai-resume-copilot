"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { apiDelete, apiGet, apiPutJson } from "../lib/api";
import type { CareerMemory } from "../lib/career";
import { Button } from "./ui/Button";
import { LoadingBlock } from "./ui/LoadingBlock";
import { StatusBadge } from "./ui/StatusBadge";

const suggestedKeys = [
  { key: "target.role", label: "Target role", category: "preferences" },
  { key: "target.location", label: "Preferred location", category: "preferences" },
  { key: "target.work_mode", label: "Work mode", category: "preferences" },
  { key: "compensation.minimum", label: "Minimum compensation", category: "constraints" },
  { key: "writing.tone", label: "Writing tone", category: "communication" },
];

export default function CareerMemoryPanel() {
  const queryClient = useQueryClient();
  const [memoryKey, setMemoryKey] = useState(suggestedKeys[0].key);
  const [value, setValue] = useState("");
  const selected = suggestedKeys.find((item) => item.key === memoryKey) || suggestedKeys[0];
  const memory = useQuery({ queryKey: ["career-memory"], queryFn: () => apiGet<CareerMemory[]>("/v1/career-memory") });
  const save = useMutation({
    mutationFn: () => apiPutJson<CareerMemory>("/v1/career-memory", { category: selected.category, memory_key: memoryKey, value }),
    onSuccess: async () => {
      setValue("");
      await queryClient.invalidateQueries({ queryKey: ["career-memory"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete(`/v1/career-memory/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["career-memory"] }),
  });

  return (
    <section className="surface-panel p-5 sm:p-7">
      <div className="flex items-start gap-4">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary"><Brain size={20} /></span>
        <div><p className="eyebrow">Career Memory</p><h2 className="mt-1 text-xl font-black text-neutral-100">Facts HireWiz may reuse</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-500">Only entries shown here are remembered. Every value is user-approved, editable, and deletable.</p></div>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-[180px_1fr_auto] sm:items-end">
        <label className="grid gap-2 text-sm font-semibold text-neutral-300">Memory type<select className="field-control" value={memoryKey} onChange={(event) => setMemoryKey(event.target.value)}>{suggestedKeys.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
        <label className="grid gap-2 text-sm font-semibold text-neutral-300">Value<input className="field-control" value={value} onChange={(event) => setValue(event.target.value)} placeholder={`Add ${selected.label.toLowerCase()}`} /></label>
        <Button disabled={!value.trim() || save.isPending} onClick={() => save.mutate()}><Plus size={16} /> Save</Button>
      </div>
      {save.isError ? <p className="mt-3 text-sm text-coral">{save.error instanceof Error ? save.error.message : "Could not save memory."}</p> : null}

      <div className="mt-7">
        {memory.isLoading ? <LoadingBlock rows={3} /> : null}
        {!memory.isLoading && !memory.data?.length ? <p className="border-y border-white/10 py-6 text-sm text-neutral-600">No reusable career memory has been approved.</p> : null}
        <div className="divide-y divide-white/10 border-t border-white/10">
          {(memory.data || []).map((entry) => (
            <div key={entry.id} className="grid gap-3 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
              <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-bold text-neutral-300">{suggestedKeys.find((item) => item.key === entry.memory_key)?.label || entry.memory_key}</p><StatusBadge tone="teal">user approved</StatusBadge></div><p className="mt-1 text-sm text-neutral-500">{typeof entry.value === "string" ? entry.value : JSON.stringify(entry.value)}</p></div>
              <Button size="icon" variant="ghost" aria-label="Delete memory" onClick={() => remove.mutate(entry.id)}><Trash2 size={16} /></Button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
