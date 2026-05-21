import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/pricing")({
  head: () => ({
    meta: [
      { title: "RIDES4U Pricing" },
      { name: "description", content: "Understand how RIDES4U ride fares are calculated." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/pricing" }],
  }),
  component: PricingPage,
});

function PricingPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">Pricing</h1>
      <p className="mt-4 text-sm text-muted-foreground">
        Fares are based on base fare, distance, time, taxes and applicable promotions.
      </p>
      <Link to="/" className="mt-8 inline-block text-sm underline">Book a ride</Link>
    </main>
  );
}
