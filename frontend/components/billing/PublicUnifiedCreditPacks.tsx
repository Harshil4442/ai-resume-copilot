"use client";

import { useRouter } from "next/navigation";
import { UnifiedCreditPacks } from "./UnifiedCreditPacks";
import type { UnifiedCreditCatalog } from "../../lib/unifiedCredits";

export default function PublicUnifiedCreditPacks({ catalog }: { catalog: UnifiedCreditCatalog }) {
  const router = useRouter();
  return <UnifiedCreditPacks catalog={catalog} onRefresh={() => router.refresh()}
    onReview={(sku) => router.push(`/billing?sku=${encodeURIComponent(sku)}`)} />;
}
