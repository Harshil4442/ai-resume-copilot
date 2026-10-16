import { NextRequest } from "next/server";
import { handleAccount } from "../../../../lib/accountTransportServer";
export const runtime = "nodejs";
async function handle(request: NextRequest, context: { params: Promise<{ operation: string }> }) {
  return handleAccount(request, (await context.params).operation);
}
export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
