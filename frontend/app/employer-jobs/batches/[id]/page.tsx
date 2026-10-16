"use client";

import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useParams, useRouter } from "next/navigation";

import { LoadingBlock } from "../../../../components/ui/LoadingBlock";

const BatchPanel = dynamic(() => import("../../../../components/EmployerBatchPanel"), { loading: () => <LoadingBlock rows={3} /> });

export default function EmployerBatchPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const client = useQueryClient();
  return <main className="app-page"><div className="page-container"><h1 className="font-display text-3xl">Your exact application batch</h1><BatchPanel key={id} id={id} onClose={() => router.push("/employer-jobs")} onChange={() => {
    void client.invalidateQueries({ queryKey: ["employer-jobs", "catalog"] });
    void client.invalidateQueries({ queryKey: ["employer-jobs", "applications"] });
    window.dispatchEvent(new Event("refresh_analysis_units"));
  }} /></div></main>;
}
