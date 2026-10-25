import { NextRequest } from "next/server";
import { browserAssistanceGateway } from "../../../../../lib/browserAssistanceGateway";

export async function POST(request: NextRequest, context: { params: Promise<{ operation: string }> }) {
  return browserAssistanceGateway(request, (await context.params).operation, false);
}
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 45;
