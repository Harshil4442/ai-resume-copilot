import NextAuth from "next-auth";
import { authOptions } from "../../../../lib/authOptions";
import { NextRequest } from "next/server";
import { guardAuthRequest } from "../../../../lib/candidateAuthServer";

const handler = NextAuth(authOptions);

type Context = { params: Promise<{ nextauth: string[] }> };
async function guarded(request: NextRequest, context: Context) {
  const path = (await context.params).nextauth;
  const checked = await guardAuthRequest(request, path[0], path[1]);
  if (checked.response) return checked.response;
  return handler(checked.request!, context);
}
export { guarded as GET, guarded as POST };
