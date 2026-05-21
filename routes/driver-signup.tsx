import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/driver-signup")({
  head: () => ({
    meta: [
      { title: "Driver Signup - RIDES4U" },
      { name: "description", content: "Join RIDES4U as a driver and start earning." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/driver-signup" }],
  }),
  component: DriverSignupPage,
});

function DriverSignupPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">Driver Signup</h1>
      <p className="mt-4 text-sm text-muted-foreground">Complete onboarding with your phone and vehicle documents.</p>
      <Link to="/auth/rider-signup" className="mt-8 inline-block text-sm underline">Start driver onboarding</Link>
    </main>
  );
}
