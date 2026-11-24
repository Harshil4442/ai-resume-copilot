"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

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

type MemoryDraft = { category: string; memoryKey: string; value: string; revision: number };
type MemoryEntry = Pick<CareerMemory, "id" | "memory_key" | "value">;

function memoryEntries(value: unknown): MemoryEntry[] {
  if (!Array.isArray(value) || !value.every((entry) => entry !== null && typeof entry === "object"
    && !Array.isArray(entry) && typeof entry.id === "string" && entry.id.length > 0
    && typeof entry.memory_key === "string" && entry.memory_key.length > 0 && Object.hasOwn(entry, "value"))) {
    throw new Error("Career Memory could not be verified.");
  }
  return value;
}

export default function CareerMemoryPanel() {
  const queryClient = useQueryClient();
  const [memoryKey, setMemoryKey] = useState(suggestedKeys[0].key);
  const [value, setValue] = useState("");
  const mounted = useRef(false), readPending = useRef(false), actionPending = useRef(false);
  const editRevision = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const selected = suggestedKeys.find((item) => item.key === memoryKey) || suggestedKeys[0];
  const memory = useQuery({ queryKey: ["career-memory"],
    queryFn: async ({ signal }) => memoryEntries(await apiGet<unknown>("/v1/career-memory", signal)),
    retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const verified = memory.isSuccess && !memory.isFetching;
  const save = useMutation({
    mutationFn: (draft: MemoryDraft) => apiPutJson<CareerMemory>("/v1/career-memory", {
      category: draft.category, memory_key: draft.memoryKey, value: draft.value }),
    onSuccess: async (_result, draft) => {
      if (!mounted.current) return;
      await queryClient.invalidateQueries({ queryKey: ["career-memory"] });
      const current = queryClient.getQueryState(["career-memory"]);
      if (mounted.current && current?.status === "success" && current.fetchStatus === "idle"
        && editRevision.current === draft.revision) setValue("");
    },
    onSettled: () => { actionPending.current = false; },
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiDelete(`/v1/career-memory/${id}`),
    onSuccess: () => { if (mounted.current) return queryClient.invalidateQueries({ queryKey: ["career-memory"] }); },
    onSettled: () => { actionPending.current = false; },
  });
  function retryMemory() {
    if (readPending.current || memory.isFetching || actionPending.current) return;
    readPending.current = true;
    void memory.refetch({ cancelRefetch: false }).finally(() => { readPending.current = false; });
  }
  function saveDraft() {
    if (!verified || readPending.current || actionPending.current || !value.trim()) return;
    actionPending.current = true;
    save.mutate({ category: selected.category, memoryKey, value, revision: editRevision.current });
  }
  function deleteMemory(id: string) {
    if (!verified || readPending.current || actionPending.current) return;
    actionPending.current = true;
    remove.mutate(id);
  }

  return (
    <section className="surface-panel p-5 sm:p-7">
      <div className="flex items-start gap-4">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/8 text-primary"><Brain size={20} /></span>
        <div><p className="eyebrow">Career Memory</p><h2 className="mt-1 font-display text-2xl font-normal text-foreground">Facts HireWiz may reuse</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-500">Review the facts HireWiz may reuse. Every saved value is user-approved, editable, and deletable.</p></div>
      </div>

      <fieldset disabled={!verified} aria-label="Edit Career Memory" className="mt-6 grid gap-3 sm:grid-cols-[180px_1fr_auto] sm:items-end">
        <label className="grid gap-2 text-sm font-semibold text-foreground">Memory type<select className="field-control" value={memoryKey} onChange={(event) => { editRevision.current += 1; setMemoryKey(event.target.value); }}>{suggestedKeys.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
        <label className="grid gap-2 text-sm font-semibold text-foreground">Value<input className="field-control" value={value} onChange={(event) => { editRevision.current += 1; setValue(event.target.value); }} placeholder={`Add ${selected.label.toLowerCase()}`} /></label>
        <Button disabled={!value.trim() || save.isPending || remove.isPending} onClick={saveDraft}><Plus size={16} /> Save</Button>
      </fieldset>
      {save.isError ? <p className="mt-3 text-sm text-coral">{save.error instanceof Error ? save.error.message : "Could not save memory."}</p> : null}
      {remove.isError ? <p role="alert" className="mt-3 text-sm text-coral">The deletion could not be confirmed. Check your saved facts before trying again.</p> : null}

      <div className="mt-7">
        {memory.isPending ? <LoadingBlock rows={3} /> : null}
        {memory.isError ? <div className="space-y-3" role="alert"><p className="text-sm text-coral">Career Memory is unavailable. Retry to review or edit your saved facts.</p><Button variant="secondary" onClick={retryMemory} disabled={memory.isFetching || save.isPending || remove.isPending}>{memory.isFetching ? "Loading saved facts…" : "Retry Career Memory"}</Button></div> : null}
        {memory.isSuccess && memory.isFetching ? <p className="mb-3 text-sm text-muted-foreground" role="status">Refreshing saved facts…</p> : null}
        {memory.isSuccess && !memory.data.length ? <p className="border-y border-border py-6 text-sm text-neutral-600">No reusable career memory has been approved.</p> : null}
        <div className="divide-y divide-border border-t border-border">
          {(memory.isSuccess ? memory.data : []).map((entry) => (
            <div key={entry.id} className="grid gap-3 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
              <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-bold text-foreground">{suggestedKeys.find((item) => item.key === entry.memory_key)?.label || entry.memory_key}</p><StatusBadge tone="teal">user approved</StatusBadge></div><p className="mt-1 text-sm text-neutral-500">{typeof entry.value === "string" ? entry.value : JSON.stringify(entry.value)}</p></div>
              <Button size="icon" variant="ghost" aria-label="Delete memory" disabled={!verified || save.isPending || remove.isPending} onClick={() => deleteMemory(entry.id)}><Trash2 size={16} /></Button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
