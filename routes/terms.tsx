import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/terms")({
  head: () => ({
    meta: [
      { title: "RIDES4U Terms" },
      { name: "description", content: "Terms and conditions for RIDES4U platform usage." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/terms" }],
  }),
  component: TermsPage,
});

function TermsPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">Terms & Conditions</h1>
      <p className="mt-4 text-sm text-muted-foreground">By using RIDES4U, you agree to platform terms and safety rules.</p>
      <Link to="/privacy" className="mt-8 inline-block text-sm underline">Read privacy policy</Link>
    </main>
  );
}
