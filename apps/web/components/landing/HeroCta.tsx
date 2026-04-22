"use client";

import Link from "next/link";
import { useAuth, useClerk } from "@clerk/nextjs";

/**
 * Hero CTA buttons for the landing page.
 *
 * Uses Clerk's imperative `useClerk().openSignUp() / openSignIn()` API
 * instead of `<SignUpButton>` / `<SignInButton>`, because those wrapper
 * components call `React.Children.only(children)` and mis-count children
 * when they're rendered from a server component (RSC serialisation can
 * deliver the single child as a tuple that Clerk sees as an array).
 * Imperative opens avoid the issue entirely.
 */
export function HeroCta() {
  const { isLoaded, isSignedIn } = useAuth();
  const { openSignUp, openSignIn } = useClerk();

  if (!isLoaded) {
    // Render the buttons optimistically as signed-out so the layout stays
    // stable — openSignUp/openSignIn are no-ops before Clerk is ready.
  }

  if (isSignedIn) {
    return (
      <Link href="/case" className="btn-indigo">
        Continue to the council
        <span aria-hidden>→</span>
      </Link>
    );
  }

  return (
    <>
      <button
        type="button"
        className="btn-indigo"
        onClick={() => openSignUp({})}
      >
        Begin a consultation
        <span aria-hidden>→</span>
      </button>
      <button
        type="button"
        className="btn-ghost"
        onClick={() => openSignIn({})}
      >
        I already have an account
      </button>
    </>
  );
}
