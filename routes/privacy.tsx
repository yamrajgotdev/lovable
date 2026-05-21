import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/privacy")({
  head: () => ({
    meta: [
      { title: "RIDES4U Privacy Policy" },
      { name: "description", content: "How RIDES4U collects and protects your data." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/privacy" }],
  }),
  component: PrivacyPage,
});

function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">Privacy Policy</h1>
      <p className="mt-4 text-sm text-muted-foreground">
        RIDES4U uses your information to operate rides, payments, and support safely.
      </p>
      <Link to="/terms" className="mt-8 inline-block text-sm underline">Read terms</Link>
    </main>
  );
}
