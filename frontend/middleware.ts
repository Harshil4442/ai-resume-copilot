export { default } from "next-auth/middleware";

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/resume/:path*",
    "/jobs/:path*",
    "/learning/:path*",
    "/profile/:path*",
  ],
};
