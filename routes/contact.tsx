import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/contact")({
  head: () => ({
    meta: [
      { title: "Contact RIDES4U" },
      { name: "description", content: "Get support and contact details for RIDES4U." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/contact" }],
  }),
  component: ContactPage,
});

function ContactPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">Contact</h1>
      <p className="mt-4 text-sm text-muted-foreground">Need help? Reach out via the in-app support flow.</p>
      <Link to="/support" className="mt-8 inline-block text-sm underline">Go to support</Link>
    </main>
  );
}
