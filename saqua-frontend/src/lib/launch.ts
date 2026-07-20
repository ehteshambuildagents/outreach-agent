/**
 * Pre-launch switch.
 *
 * While this is true, public signup is closed: every "get started" CTA points at
 * the waitlist instead, and /sign-up refuses to render the Clerk form. Existing
 * accounts can still sign IN — closing signup must never lock out the people who
 * already have access.
 *
 * Flip to false on launch day and every CTA reopens at once. It is a constant
 * rather than an env var so that reopening is a reviewable commit, not a console
 * change someone makes at 2am and forgets.
 */
export const PRELAUNCH = true;

/** Where every pre-launch CTA sends people. */
export const WAITLIST_ANCHOR = "/#waitlist";
