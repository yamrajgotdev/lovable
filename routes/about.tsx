import { createFileRoute, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/about")({
  head: () => ({
    meta: [
      { title: "About RIDES4U" },
      { name: "description", content: "Learn about RIDES4U and our ride-hailing mission." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/about" }],
  }),
  component: AboutPage,
});

function AboutPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-3xl font-bold">About RIDES4U</h1>
      <p className="mt-4 text-sm text-muted-foreground">
        RIDES4U connects passengers and drivers for safe, fast local rides across bike, auto and e-rickshaw categories.
      </p>
      <Link to="/" className="mt-8 inline-block text-sm underline">Back to home</Link>
    </main>
  );
}
