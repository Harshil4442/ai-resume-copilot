import { NextRequest } from "next/server";
import { manualSubmissionGateway } from "../../../../../lib/manualSubmissionGateway";

export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  return manualSubmissionGateway(request, (await context.params).id);
}
