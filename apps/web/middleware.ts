import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Everything is protected by default except the sign-in/sign-up pages
// themselves and static assets. As new public pages are added (marketing
// pages in later phases, for instance) add them to this matcher.
const isPublicRoute = createRouteMatcher([
  "/sign-in(.*)",
  "/sign-up(.*)",
]);

export default clerkMiddleware((auth, req) => {
  if (!isPublicRoute(req)) {
    auth().protect();
  }
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
