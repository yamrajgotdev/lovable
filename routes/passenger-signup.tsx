import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/passenger-signup")({
  head: () => ({
    meta: [
      { title: "Passenger Signup - RIDES4U" },
      { name: "description", content: "Create your RIDES4U passenger account and book rides." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/passenger-signup" }],
  }),
  component: PassengerSignupPage,
});

function PassengerSignupPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">Passenger Signup</h1>
      <p className="mt-4 text-sm text-muted-foreground">Create your account to start booking rides in seconds.</p>
      <Link to="/auth/passenger-signup" className="mt-8 inline-block text-sm underline">Create passenger account</Link>
    </main>
  );
}
