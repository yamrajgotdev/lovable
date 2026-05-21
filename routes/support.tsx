import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/support")({
  head: () => ({
    meta: [
      { title: "RIDES4U Support" },
      { name: "description", content: "Customer and driver support resources for RIDES4U." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/support" }],
  }),
  component: SupportPage,
});

function SupportPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">Support</h1>
      <p className="mt-4 text-sm text-muted-foreground">Use the in-app support ticket flow for ride or payment issues.</p>
      <Link to="/contact" className="mt-8 inline-block text-sm underline">Contact page</Link>
    </main>
  );
}
